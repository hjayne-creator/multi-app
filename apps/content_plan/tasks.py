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
                    
                    # Clear any existing themes for this job
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
                    db.session.commit()
                    
                    return {'status': 'awaiting_selection'}
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

@celery.task(bind=True, autoretry_for=(), retry=False)
def continue_workflow_after_selection_task(self, job_id):
    """Celery task to continue workflow after theme selection, using in_progress flag for concurrency control"""
    logger.info(f"Starting continue_workflow_after_selection_task for job_id: {job_id}")
    
    try:
        # Ensure we use the Flask app context
        with flask_app.app_context():
            # Log task start
            logger.info(f"[Job {job_id}] Starting post-theme selection workflow")
            
            # Refresh the session to avoid stale data
            db.session.expire_all()
            
            # First, check if job exists
            job = Job.query.get(job_id)
            if not job:
                logger.error(f"[Job {job_id}] Job not found in database")
                return {'status': 'error', 'message': f'Job {job_id} not found'}
            
            # Log the current job status
            logger.info(f"[Job {job_id}] Current status: {job.status}, in_progress: {job.in_progress}")
            
            # Atomically claim the job for processing
            try:
                result = db.session.query(Job).filter(
                    Job.id == job_id,
                    Job.in_progress == False
                ).update({'in_progress': True})
                db.session.commit()
                logger.info(f"[Job {job_id}] Attempted to claim job, result: {result}")
                
                if result == 0:
                    logger.warning(f"[Job {job_id}] Skipping: in_progress already True (another worker is processing)")
                    return {'status': 'skipped', 'message': 'Job is already being processed by another worker.'}
            except Exception as e:
                logger.error(f"[Job {job_id}] Error claiming job: {str(e)}")
                db.session.rollback()
                return {'status': 'error', 'message': f'Failed to claim job: {str(e)}'}
            
            # Reload job after update
            job = db.session.query(Job).get(job_id)
            db.session.refresh(job)
            
            # Add a message indicating the task has started
            add_message_to_job(job, "🚀 Continuing workflow with selected theme")
            db.session.commit()
            
            try:
                # Load workflow manager
                workflow_manager = WorkflowManager()
                if not job.workflow_data:
                    job.workflow_data = {}
                workflow_manager.load_state(job.workflow_data)
                
                # Get the selected theme
                selected_theme = Theme.query.filter_by(job_id=job_id, is_selected=True).first()
                if not selected_theme:
                    logger.error(f"[Job {job_id}] No theme was selected")
                    job.status = 'error'
                    job.error = "No theme was selected"
                    job.in_progress = False
                    add_message_to_job(job, "❌ Error: No theme was selected")
                    db.session.commit()
                    return {'status': 'error', 'message': "No theme was selected"}
                
                logger.info(f"[Job {job_id}] Selected theme: {selected_theme.title}")
                
                # Update task state
                self.update_state(state='PROGRESS',
                                meta={'current': 60, 'total': 100,
                                      'status': 'Processing selected theme'})
                
                # --- Step 1: Content Cluster Generation ---
                add_message_to_job(job, "📝 STRATEGY PHASE: Creating content clusters")
                add_message_to_job(job, f"🎯 Processing selected theme: {selected_theme.title}")
                db.session.commit()
                
                strategy_message = f"""
                ## Brand Brief
                {job.brand_brief}
                
                ## Selected Theme
                **{selected_theme.title}**
                {selected_theme.description}
                
                \nPlease create a content cluster framework based on this theme.
                """
                
                try:
                    logger.info(f"[Job {job_id}] Starting content cluster generation")
                    content_cluster = run_agent_with_openai(CONTENT_STRATEGIST_CLUSTER_PROMPT, strategy_message)
                    if not content_cluster or len(content_cluster.strip()) < 100:
                        raise Exception("OpenAI API returned an empty or too short response for content cluster generation.")
                    job.content_cluster = content_cluster
                    job.progress = 80
                    add_message_to_job(job, "✅ Content clusters created")
                    db.session.commit()
                    logger.info(f"[Job {job_id}] Content clusters created successfully")
                    
                    self.update_state(state='PROGRESS',
                                     meta={'current': 80, 'total': 100,
                                           'status': 'Content clusters created'})
                except Exception as e:
                    logger.error(f"[Job {job_id}] Error in content cluster generation: {str(e)}")
                    logger.error(traceback.format_exc())
                    job.status = 'error'
                    job.error = f"Error in content cluster generation: {str(e)}"
                    job.in_progress = False
                    add_message_to_job(job, f"❌ Error in content cluster generation: {str(e)}")
                    db.session.commit()
                    return {'status': 'error', 'message': str(e)}

                # --- Step 2: Article Ideation ---
                add_message_to_job(job, "💡 ARTICLE IDEATION PHASE: Developing content ideas")
                db.session.commit()
                
                # Idempotency check: skip OpenAI call if article_ideas already exists and is valid
                if job.article_ideas and len(job.article_ideas.strip()) >= 100:
                    logger.info(f"[Job {job_id}] Article ideas already exist, skipping OpenAI call")
                    add_message_to_job(job, "ℹ️ Article ideas already exist, skipping OpenAI call.")
                    article_ideas = job.article_ideas
                else:
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
                    try:
                        logger.info(f"[Job {job_id}] Starting article ideation")
                        article_ideas = run_agent_with_openai(CONTENT_WRITER_PROMPT, ideation_message)
                        if not article_ideas or len(article_ideas.strip()) < 100:
                            raise Exception("OpenAI API returned an empty or too short response for article ideation.")
                        job.article_ideas = article_ideas
                        db.session.commit()
                        add_message_to_job(job, "✅ Article ideas generated")
                        logger.info(f"[Job {job_id}] Article ideas generated successfully")
                    except Exception as e:
                        logger.error(f"[Job {job_id}] Error in article ideation: {str(e)}")
                        logger.error(traceback.format_exc())
                        job.status = 'error'
                        job.error = f"Error in article ideation: {str(e)}"
                        job.in_progress = False
                        add_message_to_job(job, f"❌ Error in article ideation: {str(e)}")
                        db.session.commit()
                        return {'status': 'error', 'message': str(e)}
                
                job.progress = 90
                db.session.commit()
                self.update_state(state='PROGRESS',
                                 meta={'current': 90, 'total': 100,
                                       'status': 'Article ideas generated'})

                # --- Step 3: Final Plan Generation ---
                add_message_to_job(job, "📊 EDITING PHASE: Adding final touches to the content plan")
                db.session.commit()
                
                # Idempotency check: skip OpenAI call if final_plan already exists and is valid
                if job.final_plan and len(job.final_plan.strip()) >= 100:
                    logger.info(f"[Job {job_id}] Final plan already exists, skipping OpenAI call")
                    add_message_to_job(job, "ℹ️ Final plan already exists, skipping OpenAI call.")
                    final_plan = job.final_plan
                else:
                    # Validate required fields before making the API call
                    if not job.brand_brief or not job.search_analysis:
                        error_msg = "Missing required data for final plan generation"
                        logger.error(f"[Job {job_id}] {error_msg}")
                        job.status = 'error'
                        job.error = error_msg
                        job.in_progress = False
                        add_message_to_job(job, f"❌ {error_msg}")
                        db.session.commit()
                        return {'status': 'error', 'message': error_msg}

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
                    try:
                        logger.info(f"[Job {job_id}] Starting final plan generation")
                        final_plan = run_agent_with_openai(CONTENT_EDITOR_PROMPT, finalization_message)
                        if not final_plan or len(final_plan.strip()) < 100:
                            raise Exception("OpenAI API returned an empty or too short response for final plan generation.")
                        
                        # Validate the response format
                        if not any(marker in final_plan for marker in ['##', '#']):
                            raise Exception("Generated content plan is missing proper markdown formatting")
                        
                        job.final_plan = final_plan
                        logger.info(f"[Job {job_id}] Final plan generated successfully")
                    except Exception as e:
                        logger.error(f"[Job {job_id}] Error in final plan generation: {str(e)}")
                        logger.error(traceback.format_exc())
                        error_msg = f"Error in final plan generation: {str(e)}"
                        job.status = 'error'
                        job.error = error_msg
                        job.in_progress = False
                        add_message_to_job(job, f"❌ {error_msg}")
                        db.session.commit()
                        return {'status': 'error', 'message': str(e)}
                
                # Complete the job
                logger.info(f"[Job {job_id}] Completing job")
                job.progress = 100
                workflow_manager.advance_phase()  # To COMPLETION
                job.workflow_data = workflow_manager.save_state()
                job.current_phase = workflow_manager.current_phase
                job.status = 'completed'
                job.completed_at = datetime.now()
                job.in_progress = False
                add_message_to_job(job, "✅ Content plan completed successfully!")
                add_message_to_job(job, "🎉 Your content strategy is ready!")
                db.session.commit()
                logger.info(f"[Job {job_id}] Task completed successfully")
                return {'status': 'completed'}

            except Exception as e:
                logger.error(f"[Job {job_id}] Error in post-theme selection workflow: {str(e)}")
                logger.error(traceback.format_exc())
                error_msg = f"Error in theme selection workflow: {str(e)}"
                job.status = 'error'
                job.error = error_msg
                job.in_progress = False
                add_message_to_job(job, f"❌ {error_msg}")
                db.session.commit()
                return {'status': 'error', 'message': error_msg}
    except Exception as e:
        logger.error(f"[Job {job_id}] Unexpected error in task: {str(e)}")
        logger.error(traceback.format_exc())
        try:
            # Try to update the job if possible
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
        return {'status': 'error', 'message': f"Unexpected error: {str(e)}"}
