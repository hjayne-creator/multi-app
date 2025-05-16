import json
import logging
from flask import current_app
from apps import db
from apps.content_plan.models import Job, Theme
from apps.content_plan.utils.scraper import scrape_website
from apps.content_plan.utils.search import search_serpapi, deduplicate_results
from apps.content_plan.utils.workflow import WorkflowManager
from apps.content_plan.utils.agents import run_agent_with_openai
from datetime import datetime
import traceback
from dotenv import load_dotenv
from apps.content_plan.prompts import (
    BRAND_BRIEF_PROMPT,
    SEARCH_ANALYSIS_PROMPT,
    CONTENT_ANALYST_PROMPT,
    CONTENT_STRATEGIST_CLUSTER_PROMPT,
    CONTENT_WRITER_PROMPT,
    CONTENT_EDITOR_PROMPT
)
import os
import time
from flask import current_app
from apps import create_app
from apps.content_plan.celery_config import celery, init_celery
import threading

# Configure logging
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Create Flask app for Celery tasks
flask_app = create_app()
# Initialize Celery with Flask app
init_celery(flask_app)

def add_message_to_job(job, message):
    """
    Safely add a message to the job's messages list,
    ensuring the list exists and is properly formatted
    """
    # Make sure job.messages is at least an empty list
    if job.messages is None:
        job.messages = []
    
    # If it's a SQLAlchemy MutableList, convert as needed
    if not isinstance(job.messages, list):
        try:
            # Try to convert to a standard Python list
            job.messages = list(job.messages)
        except:
            # If conversion fails, initialize a new list
            job.messages = []
    
    # Create message object with text and timestamp
    message_obj = {
        'text': message,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    # Create a new list with existing messages and append the new one
    current_messages = list(job.messages)  # Create a copy
    current_messages.append(message_obj)   # Append the new message
    job.messages = current_messages        # Assign the new list back
    
    # Log the action for debugging
    logger.info(f"Added message to job {job.id}: {message}")
    logger.info(f"Current message count: {len(job.messages)}")
    logger.info(f"All messages: {job.messages}")
    
    # Commit changes to ensure they're saved
    try:
        db.session.commit()
    except Exception as e:
        # If commit fails, log the error but don't raise
        logger.error(f"Failed to commit after adding message: {str(e)}")
        # Try to rollback
        try:
            db.session.rollback()
        except:
            pass

@celery.task(bind=True)
def process_workflow_task(self, job_id):
    """Celery task to process the content workflow"""
    logger.info(f"Starting process_workflow_task for job_id: {job_id}")
    
    try:
        with flask_app.app_context():
            # Get job and create a new session
            job = Job.query.get_or_404(job_id)
            
            # First check selected_theme_id for consistency
            if job.selected_theme_id is not None:
                logger.info(f"Job {job_id} has selected_theme_id={job.selected_theme_id}, ensuring it's properly set")
                # Make sure the theme is marked as selected
                theme = Theme.query.get(job.selected_theme_id)
                if theme and not theme.is_selected:
                    theme.is_selected = True
                    db.session.commit()
                    logger.info(f"Fixed theme selection state for theme {theme.id}")
            
            # Check if this job already has a selected theme
            theme_selected = Theme.query.filter_by(job_id=job_id, is_selected=True).first()
            if theme_selected:
                logger.info(f"Theme already selected for job {job_id}, skipping to continuation workflow")
                
                # Update job.selected_theme_id for consistency
                if job.selected_theme_id != theme_selected.id:
                    job.selected_theme_id = theme_selected.id
                    logger.info(f"Updated job.selected_theme_id to {theme_selected.id}")
                
                add_message_to_job(job, f"Continuing with selected theme: {theme_selected.title}")
                job.status = 'processing'
                job.current_phase = 'STRATEGY'  # Force phase to STRATEGY
                
                # Load workflow manager
                workflow_manager = WorkflowManager()
                if job.workflow_data:
                    workflow_manager.load_state(job.workflow_data)
                
                # Ensure we're in the right phase
                if workflow_manager.current_phase != 'STRATEGY':
                    workflow_manager.set_phase('STRATEGY')
                    job.workflow_data = workflow_manager.save_state()
                    job.current_phase = workflow_manager.current_phase
                
                db.session.commit()
                
                # Start processing the selected theme in a background thread
                thread = threading.Thread(target=process_selected_theme, args=(job_id,))
                thread.daemon = True
                thread.start()
                return {'status': 'processing'}
            
            # Initialize workflow
            logger.info("Initializing workflow")
            job.status = 'processing'
            job.progress = 0
            if not job.messages:
                job.messages = []
            add_message_to_job(job, "Starting workflow processing...")
            add_message_to_job(job, "Preparing to analyze website content and keywords...")
            db.session.commit()
            
            workflow_manager = WorkflowManager()
            job.workflow_data = workflow_manager.save_state()
            job.current_phase = workflow_manager.current_phase
            db.session.commit()
            
            # Update task state
            self.update_state(state='PROGRESS',
                            meta={'current': 0, 'total': 100,
                                  'status': 'Initializing workflow'})
            
            # Scrape website
            add_message_to_job(job, f"🔍 Retrieving content from {job.website_url}...")
            db.session.commit()
            
            try:
                website_content_result = scrape_website(job.website_url)
            except Exception as e:
                logger.error(f"Error scraping website: {str(e)}")
                error_message = f"Failed to scrape website content: {str(e)}"
                job.status = 'error'
                job.error = error_message
                add_message_to_job(job, f"❌ Error: {error_message}")
                db.session.commit()
                return {'status': 'error', 'message': error_message}

            if not website_content_result.get("success"):
                job.status = 'error'
                job.error = website_content_result.get("error", "Unknown error")
                add_message_to_job(job, f"❌ Error: {job.error}")
                db.session.commit()
                return {'status': 'error', 'message': job.error}

            # Compose a concise content string for OpenAI
            website_content = f"""\nTitle: {website_content_result.get('title', '')}\nDescription: {website_content_result.get('description', '')}\nBody: {website_content_result.get('body', '')}\n"""

            job.website_content_length = len(website_content)
            job.progress = 10
            add_message_to_job(job, f"✅ Successfully retrieved {len(website_content)} characters of content")
            db.session.commit()
            
            # Update task state
            self.update_state(state='PROGRESS',
                            meta={'current': 10, 'total': 100,
                                  'status': 'Website content retrieved'})
            
            # Search for keywords
            add_message_to_job(job, f"🔍 Starting keyword research for: {', '.join(job.keywords)}")
            db.session.commit()
            
            all_search_results = []
            failed_keywords = []
            
            # Get API key from config
            serpapi_key = current_app.config.get('SERPAPI_API_KEY')
            if not serpapi_key:
                error_msg = "❌ SERPAPI_API_KEY not found in configuration"
                job.status = 'error'
                job.error = error_msg
                add_message_to_job(job, error_msg)
                db.session.commit()
                return {'status': 'error', 'message': error_msg}
            
            for idx, keyword in enumerate(job.keywords):
                try:
                    add_message_to_job(job, f"🔍 Searching for keyword: {keyword}")
                    db.session.commit()
                    
                    results = search_serpapi(keyword, serpapi_key)
                    if results:
                        all_search_results.extend(results)
                        add_message_to_job(job, f"✅ Found {len(results)} results for keyword: {keyword}")
                    else:
                        failed_keywords.append(keyword)
                        add_message_to_job(job, f"⚠️ No results found for keyword: {keyword}")
                    db.session.commit()
                except Exception as e:
                    failed_keywords.append(keyword)
                    add_message_to_job(job, f"❌ Error searching for '{keyword}': {str(e)}")
                    db.session.commit()
                
                # Update progress for each keyword
                progress = 10 + int((idx + 1) / len(job.keywords) * 10)
                job.progress = progress
                db.session.commit()
                
                self.update_state(state='PROGRESS',
                                meta={'current': progress, 'total': 100,
                                      'status': f'Searching keyword {idx + 1}/{len(job.keywords)}'})
            
            # Deduplicate results
            add_message_to_job(job, "🔄 Deduplicating search results...")
            db.session.commit()
            
            unique_results = deduplicate_results(all_search_results)
            total_results = len(unique_results)
            
            if total_results == 0:
                job.status = 'error'
                job.error = "No search results were found for any keywords. Try different keywords."
                add_message_to_job(job, "❌ No search results were found for any keywords. Try different keywords.")
                db.session.commit()
                return {'status': 'error', 'message': "No search results found"}
            
            job.search_results = unique_results
            job.search_results_count = total_results
            job.progress = 20
            add_message_to_job(job, f"✅ Found {total_results} unique search results after deduplication")
            db.session.commit()
            
            # Update task state
            self.update_state(state='PROGRESS',
                            meta={'current': 20, 'total': 100,
                                  'status': 'Search results processed'})
            
            # Begin agent workflow
            add_message_to_job(job, "🤖 Starting AI analysis of content and search results...")
            db.session.commit()
            
            # Advance workflow to RESEARCH phase
            workflow_manager.advance_phase()  # To RESEARCH
            job.workflow_data = workflow_manager.save_state()
            job.current_phase = workflow_manager.current_phase
            db.session.commit()
            
            # Research phase
            add_message_to_job(job, "📊 RESEARCH PHASE: Analyzing website content and search results")
            #add_message_to_job(job, "🔍 Extracting brand information...")
            db.session.commit()
            
            try:
                # First request: Analyze website content for brand brief
                brand_brief_message = f"""
                ## Website Content
                {website_content}
                
                Please analyze this content and provide a comprehensive Brand Brief.
                """
                
                logger.info("Starting OpenAI API call for brand brief analysis")
                brand_brief_response = run_agent_with_openai(BRAND_BRIEF_PROMPT, brand_brief_message)
                logger.info("OpenAI API call for brand brief completed successfully")
                
                # Extract brand brief
                brand_brief = ""
                if "## Brand Brief" in brand_brief_response:
                    brand_brief = brand_brief_response.split("## Brand Brief", 1)[1].strip()
                else:
                    brand_brief = brand_brief_response.strip()
                
                job.brand_brief = brand_brief
                job.progress = 30
                add_message_to_job(job, "✅ Completed brand brief analysis")
                db.session.commit()
                
                # Second request: Analyze search results
                add_message_to_job(job, "🔍 Analyzing search results...")
                db.session.commit()
                
                search_analysis_message = f"""
                ## Search Results
                {json.dumps(unique_results[:10], indent=2)}
                
                Please analyze these search results and provide a Search Results Analysis.
                """
                
                logger.info("Starting OpenAI API call for search results analysis")
                search_analysis_response = run_agent_with_openai(SEARCH_ANALYSIS_PROMPT, search_analysis_message)
                logger.info("OpenAI API call for search results analysis completed successfully")
                
                # Extract search analysis
                search_analysis = ""
                if "## Search Results Analysis" in search_analysis_response:
                    search_analysis = search_analysis_response.split("## Search Results Analysis", 1)[1].strip()
                else:
                    search_analysis = search_analysis_response.strip()
                
                job.search_analysis = search_analysis
                job.progress = 40
                add_message_to_job(job, "✅ Completed search results analysis")
                add_message_to_job(job, "📊 Moving to content theme generation...")
                db.session.commit()
                
                # Update task state
                self.update_state(state='PROGRESS',
                                meta={'current': 40, 'total': 100,
                                      'status': 'Research phase completed'})
                
                # Advance workflow to ANALYSIS phase
                workflow_manager.advance_phase()  # To ANALYSIS
                job.workflow_data = workflow_manager.save_state()
                job.current_phase = workflow_manager.current_phase
                db.session.commit()
                
                # Generate themes
                add_message_to_job(job, "🎯 ANALYSIS PHASE: Generating content themes")
                #add_message_to_job(job, "🤖 Analyzing brand brief and search results for theme opportunities...")
                db.session.commit()
                
                user_message = f"""
                ## Brand Brief
                {job.brand_brief}
                
                ## Search Analysis
                {job.search_analysis}
                
                Please generate 6 distinct content themes based on this information that will be used for blog posts for the provided brand.
                """
                
                themes_response = run_agent_with_openai(CONTENT_ANALYST_PROMPT, user_message)
                
                # Parse themes
                if "## Content Themes" in themes_response:
                    themes_text = themes_response.split("## Content Themes", 1)[1].strip()
                    
                    import re
                    pattern = r'(\d+)\.\s+\*\*(.*?)\*\*\s+(.*?)(?=\d+\.\s+\*\*|\Z)'
                    matches = re.finditer(pattern, themes_text, re.DOTALL)
                    
                    # Clear any existing themes for this job ONLY if no theme is selected
                    if not Theme.query.filter_by(job_id=job_id, is_selected=True).first():
                        Theme.query.filter_by(job_id=job_id).delete()
                    
                        for match in matches:
                            theme_num = match.group(1).strip()
                            title = match.group(2).strip()
                            description = match.group(3).strip()
                            
                            # Create theme in database
                            theme = Theme(
                                job_id=job_id,
                                title=title,
                                description=description,
                                is_selected=False
                            )
                            db.session.add(theme)
                    
                        db.session.commit()
                        theme_count = len(list(re.finditer(pattern, themes_text)))
                        add_message_to_job(job, f"✅ Generated {theme_count} content themes")
                        add_message_to_job(job, "⏳ Waiting for theme selection...")
                        db.session.commit()
                        
                        # Advance workflow to THEME_SELECTION phase
                        workflow_manager.advance_phase()  # To THEME_SELECTION
                        job.workflow_data = workflow_manager.save_state()
                        job.current_phase = workflow_manager.current_phase
                        job.status = 'awaiting_selection'
                        job.selected_theme_id = None  # Ensure this is reset
                        db.session.commit()
                        
                        return {'status': 'awaiting_selection'}
                    else:
                        # If theme is already selected, skip to continuation
                        theme_selected = Theme.query.filter_by(job_id=job_id, is_selected=True).first()
                        logger.info(f"Theme already selected during theme generation phase, continuing with {theme_selected.title}")
                        add_message_to_job(job, f"Continuing with previously selected theme: {theme_selected.title}")
                        
                        # Update selected_theme_id for consistency
                        job.selected_theme_id = theme_selected.id
                        
                        # Set to STRATEGY phase
                        workflow_manager.set_phase('STRATEGY')
                        job.workflow_data = workflow_manager.save_state()
                        job.current_phase = workflow_manager.current_phase
                        job.status = 'processing'
                        db.session.commit()
                        
                        # Continue with the selected theme
                        return _continue_workflow_process(job_id)
                else:
                    error_msg = "❌ Failed to parse themes from AI response"
                    job.status = 'error'
                    job.error = error_msg
                    add_message_to_job(job, error_msg)
                    db.session.commit()
                    return {'status': 'error', 'message': error_msg}
                
            except Exception as e:
                error_msg = f"Error in research phase: {str(e)}"
                logger.error(error_msg, exc_info=True)
                job.status = 'error'
                job.error = error_msg
                add_message_to_job(job, f"❌ {error_msg}")
                db.session.commit()
                return {'status': 'error', 'message': error_msg}
            
    except Exception as e:
        error_msg = f"Unexpected error in workflow: {str(e)}"
        logger.error(error_msg, exc_info=True)
        if 'job' in locals():
            job.status = 'error'
            job.error = error_msg
            add_message_to_job(job, error_msg)
            db.session.commit()
        return {'status': 'error', 'message': error_msg}

# SIMPLIFIED FUNCTION REPLACING CONTINUE_WORKFLOW_AFTER_SELECTION_TASK
def process_selected_theme(job_id):
    """Process the selected theme synchronously, without using Celery"""
    logger.info(f"[Job {job_id}] Starting post-theme selection workflow")
    
    try:
        # Use Flask app context
        with flask_app.app_context():
            # Get the job
            job = Job.query.get(job_id)
            if not job:
                logger.error(f"[Job {job_id}] Job not found")
                return

            # Set job as in progress
            job.in_progress = True
            db.session.commit()

            # Add message indicating we're continuing
            add_message_to_job(job, "🚀 Continuing workflow with selected theme")
            
            # Set up workflow manager
            workflow_manager = WorkflowManager()
            if job.workflow_data:
                workflow_manager.load_state(job.workflow_data)
            
            # Ensure we're in the STRATEGY phase
            if workflow_manager.current_phase != 'STRATEGY':
                workflow_manager.set_phase('STRATEGY')
                job.workflow_data = workflow_manager.save_state()
                job.current_phase = workflow_manager.current_phase
                db.session.commit()
            
            # Get the selected theme
            selected_theme = Theme.query.filter_by(job_id=job_id, is_selected=True).first()
            if not selected_theme:
                logger.error(f"[Job {job_id}] No theme selected")
                job.status = 'error'
                job.error = "No theme selected"
                job.in_progress = False
                add_message_to_job(job, "❌ Error: No theme was selected")
                db.session.commit()
                return
            
            # Ensure selected_theme_id is set correctly
            if job.selected_theme_id != selected_theme.id:
                job.selected_theme_id = selected_theme.id
                db.session.commit()
                
            # --- Step 1: Content Cluster Generation ---
            add_message_to_job(job, "📝 STRATEGY PHASE: Creating content clusters")
            add_message_to_job(job, f"🎯 Processing selected theme: {selected_theme.title}")
            
            strategy_message = f"""
            ## Brand Brief
            {job.brand_brief}
            
            ## Selected Theme
            **{selected_theme.title}**
            {selected_theme.description}
            
            \nPlease create a content cluster framework based on this theme.
            """
            
            try:
                # Generate content clusters
                content_cluster = run_agent_with_openai(CONTENT_STRATEGIST_CLUSTER_PROMPT, strategy_message)
                if not content_cluster or len(content_cluster.strip()) < 100:
                    raise Exception("Generated content cluster is too short or empty")
                
                job.content_cluster = content_cluster
                job.progress = 80
                add_message_to_job(job, "✅ Content clusters created")
                db.session.commit()
                
                # --- Step 2: Article Ideation ---
                add_message_to_job(job, "💡 ARTICLE IDEATION PHASE: Developing content ideas")
                
                # Check if we already have article ideas
                if job.article_ideas and len(job.article_ideas.strip()) >= 100:
                    logger.info(f"[Job {job_id}] Article ideas already exist, skipping generation")
                    add_message_to_job(job, "ℹ️ Article ideas already exist, skipping generation")
                    article_ideas = job.article_ideas
                else:
                    # Generate article ideas
                    ideation_message = f"""
                    ## Brand Brief
                    {job.brand_brief}
                    
                    ## Selected Theme
                    **{selected_theme.title}**
                    {selected_theme.description}
                    
                    ## Content Cluster Framework
                    {content_cluster}
                    
                    \nPlease create article ideas based on this content framework.
                    """
                    
                    article_ideas = run_agent_with_openai(CONTENT_WRITER_PROMPT, ideation_message)
                    if not article_ideas or len(article_ideas.strip()) < 100:
                        raise Exception("Generated article ideas too short or empty")
                    
                    job.article_ideas = article_ideas
                    add_message_to_job(job, "✅ Article ideas generated")
                
                job.progress = 90
                db.session.commit()
                
                # --- Step 3: Final Plan Generation ---
                add_message_to_job(job, "📊 EDITING PHASE: Adding final touches to the content plan")
                
                # Check if we already have a final plan
                if job.final_plan and len(job.final_plan.strip()) >= 100:
                    logger.info(f"[Job {job_id}] Final plan already exists, skipping generation")
                    add_message_to_job(job, "ℹ️ Final plan already exists, skipping generation")
                    final_plan = job.final_plan
                else:
                    # Generate final plan
                    finalization_message = f"""
                    ## Brand Brief
                    {job.brand_brief}
    
                    ## Search Results Analysis
                    {job.search_analysis}
                    
                    ## Selected Theme
                    **{selected_theme.title}**
                    {selected_theme.description}
                    
                    Please create an organized and polished final content plan by reviewing and refining the brand brief and search analysis. 
                    The Pillar Topics & Articles section will be added separately.
                    """
                    
                    final_plan = run_agent_with_openai(CONTENT_EDITOR_PROMPT, finalization_message)
                    if not final_plan or len(final_plan.strip()) < 100:
                        raise Exception("Generated final plan too short or empty")
                    
                    job.final_plan = final_plan
                
                # Complete job
                job.progress = 100
                workflow_manager.advance_phase()  # To COMPLETION
                job.workflow_data = workflow_manager.save_state()
                job.current_phase = workflow_manager.current_phase
                job.status = 'completed'
                job.completed_at = datetime.now()
                add_message_to_job(job, "✅ Content plan completed successfully!")
                add_message_to_job(job, "🎉 Your content strategy is ready!")
                
            except Exception as e:
                logger.error(f"[Job {job_id}] Error in theme processing: {str(e)}")
                logger.error(traceback.format_exc())
                job.status = 'error'
                job.error = f"Error processing theme: {str(e)}"
                add_message_to_job(job, f"❌ Error: {str(e)}")
            
            finally:
                # Always reset in_progress flag
                job.in_progress = False
                db.session.commit()
    
    except Exception as e:
        logger.error(f"[Job {job_id}] Unexpected error: {str(e)}")
        logger.error(traceback.format_exc())
        # Try to update job if possible
        try:
            with flask_app.app_context():
                job = Job.query.get(job_id)
                if job:
                    job.status = 'error'
                    job.error = f"Unexpected error: {str(e)}"
                    job.in_progress = False
                    add_message_to_job(job, f"❌ Unexpected error: {str(e)}")
                    db.session.commit()
        except:
            pass

# Add a new simple method that can be called directly from routes
def start_theme_processing(job_id, theme_id):
    """Start processing the selected theme without Celery"""
    with flask_app.app_context():
        try:
            # Get job and theme
            job = Job.query.get(job_id)
            theme = Theme.query.get(theme_id)
            
            if not job or not theme:
                logger.error(f"Job {job_id} or theme {theme_id} not found")
                return False
                
            # Mark theme as selected
            theme.is_selected = True
            job.selected_theme_id = theme.id
            
            # Set job status
            job.status = 'processing'
            job.in_progress = False  # Will be set to True in process_selected_theme
            
            # Advance workflow manager
            workflow_manager = WorkflowManager()
            if job.workflow_data:
                workflow_manager.load_state(job.workflow_data)
            workflow_manager.set_phase('STRATEGY')
            job.workflow_data = workflow_manager.save_state()
            job.current_phase = 'STRATEGY'
            
            add_message_to_job(job, f"Selected theme: {theme.title}")
            db.session.commit()
            
            # Start processing in background thread
            thread = threading.Thread(target=process_selected_theme, args=(job_id,))
            thread.daemon = True
            thread.start()
            
            return True
        except Exception as e:
            logger.error(f"Error starting theme processing: {str(e)}")
            logger.error(traceback.format_exc())
            return False
