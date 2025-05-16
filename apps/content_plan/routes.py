from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash, current_app, Response
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, URL
from flask_migrate import Migrate
import uuid
import json
import os
import re
from datetime import datetime
from dotenv import load_dotenv
from apps.content_plan.config import get_config
from apps.content_plan.utils.scraper import scrape_website, validate_url
from apps.content_plan.utils.search import search_serpapi, deduplicate_results
from apps.content_plan.utils.workflow import WorkflowManager
from apps.content_plan.models import db, Job, Theme
from apps.content_plan.prompts import (
    BRAND_BRIEF_PROMPT,
    SEARCH_ANALYSIS_PROMPT,
    CONTENT_ANALYST_PROMPT,
    CONTENT_STRATEGIST_CLUSTER_PROMPT,
    CONTENT_WRITER_PROMPT,
    CONTENT_EDITOR_PROMPT
)
from sqlalchemy import text, desc

# Create CSRF protection instance
csrf = CSRFProtect()

def create_blueprint():
    # Import celery here to avoid circular imports
    from apps.content_plan.celery_config import celery
    
    bp = Blueprint('content_plan', __name__,
                   template_folder='templates',
                   static_folder='static',
                   static_url_path='/apps/content-plan/static',
                   url_prefix='/apps/content-plan')
    
    # Constants for merging article ideas into the final plan
    FINAL_PLAN_SPLIT_MARKER = "[This section will be provided separately and should not be generated.]"
    PILLAR_TOPICS_HEADING = "## Pillar Topics & Articles"

    # Forms
    class ContentWorkflowForm(FlaskForm):
        website_url = StringField('Website URL', validators=[DataRequired(), URL()])
        keywords = TextAreaField('Search Keywords (one per line or comma-separated)', validators=[DataRequired()])

    def get_celery_tasks():
        """Lazy import of celery tasks to avoid circular imports"""
        from apps.content_plan.tasks import process_workflow_task, continue_workflow_after_selection_task
        return process_workflow_task, continue_workflow_after_selection_task

    def merge_final_plan_with_articles(final_plan, article_ideas, split_marker, section_heading):
        """
        Merges the final plan with article ideas, ensuring proper placement of the Pillar Topics & Articles section.
        Also cleans the markdown for proper rendering.
        """
        if not final_plan:
            return f"{section_heading}\n\n{article_ideas}"

        # Clean up the final plan text
        # Remove leading whitespace/indentation on each line
        final_plan = "\n".join(line.strip() for line in final_plan.splitlines())
        
        # Ensure the first heading is properly formatted
        if final_plan.lstrip().startswith("# "):
            # Already correct format
            pass
        elif final_plan.lstrip().startswith("#"):
            # Missing space after #
            final_plan = final_plan.replace("#", "# ", 1)
        elif final_plan.lstrip().startswith("            # "):
            # Indented heading with code block
            final_plan = final_plan.lstrip()
        
        # Normalize multiple consecutive newlines to maximum of two
        final_plan = re.sub(r'\n{3,}', '\n\n', final_plan)
        
        # Similarly clean article_ideas
        article_ideas = "\n".join(line.strip() for line in article_ideas.splitlines())
        article_ideas = re.sub(r'\n{3,}', '\n\n', article_ideas)

        # Remove any existing duplicate sections
        final_plan = re.sub(rf"\n{section_heading}.*$", "", final_plan, flags=re.DOTALL)
        final_plan = re.sub(r"\n## Selected Theme.*$", "", final_plan, flags=re.DOTALL)
        final_plan = re.sub(r"\n## Article Ideas.*$", "", final_plan, flags=re.DOTALL)

        # If split_marker is present, insert section there
        if split_marker in final_plan:
            before, after = final_plan.split(split_marker, 1)
            return f"{before.rstrip()}\n\n{section_heading}\n\n{article_ideas}\n\n{after.lstrip()}"

        # Try to insert after '## Search Results Analysis'
        sra_match = re.search(r"(^## Search Results Analysis.*?)(?=^## |\Z)", final_plan, re.DOTALL | re.MULTILINE)
        if sra_match:
            insert_pos = sra_match.end(1)
            return final_plan[:insert_pos].rstrip() + f"\n\n{section_heading}\n\n{article_ideas}\n\n" + final_plan[insert_pos:].lstrip()

        # Fallback: append at the end
        return final_plan.rstrip() + f"\n\n{section_heading}\n\n{article_ideas}"

    @bp.route('/', methods=['GET', 'POST'])
    def index():
        try:
            form = ContentWorkflowForm()
            
            # Debug information
            current_app.logger.info(f"Method: {request.method}")
            if request.method == 'POST':
                current_app.logger.info(f"Form data: {request.form}")
                current_app.logger.info(f"Form validation: {form.validate()}")
                if form.errors:
                    current_app.logger.info(f"Form errors: {form.errors}")
            
            if form.validate_on_submit():
                current_app.logger.info("Form validated successfully")
                
                try:
                    # Create a unique job ID
                    job_id = str(uuid.uuid4())
                    
                    # Process keywords
                    keywords_text = form.keywords.data
                    keywords = [k.strip() for k in re.split(r'[,\n]', keywords_text) if k.strip()]
                    
                    if not keywords:
                        flash("Please enter at least one valid keyword", "error")
                        return render_template('content_plan_index.html', form=form)
                    
                    website_url = form.website_url.data
                    if not validate_url(website_url):
                        flash("Please enter a valid URL including http:// or https://", "error")
                        return render_template('content_plan_index.html', form=form)
                    
                    # Create new job in database
                    new_job = Job(
                        id=job_id,
                        status='initialized',
                        website_url=website_url,
                        keywords=keywords,
                        current_phase='INITIALIZATION',
                        progress=0,
                        workflow_data={},
                        messages=[{
                            'text': "Job initialized, preparing to process...",
                            'timestamp': datetime.utcnow().isoformat()
                        }]
                    )
                    db.session.add(new_job)
                    db.session.commit()
                    
                    current_app.logger.info(f"Created job {job_id}")
                    
                    # Redirect to processing page
                    return redirect(url_for('content_plan.process_job', job_id=job_id))
                except Exception as e:
                    current_app.logger.error(f"Error creating job: {str(e)}")
                    flash(f"An error occurred: {str(e)}", "error")
                    return render_template('content_plan_index.html', form=form)
            else:
                if request.method == 'POST':
                    current_app.logger.info("Form validation failed")
                    flash("Please correct the errors in the form", "error")
            
            return render_template('content_plan_index.html', form=form)
        except Exception as e:
            current_app.logger.error(f"Unexpected error in index route: {str(e)}")
            flash("An unexpected error occurred. Please try again.", "error")
            return render_template('content_plan_index.html', form=ContentWorkflowForm())

    @bp.route('/process/<job_id>', methods=['GET'])
    def process_job(job_id):
        job = Job.query.get_or_404(job_id)
        current_app.logger.info(f"Processing job {job_id}")
        
        # If this is the first time viewing the process page, start the job
        if job.status == 'initialized':
            job.status = 'processing'
            job.messages.append("Starting content research workflow...")
            db.session.commit()
            
            # Start processing in Celery
            process_workflow_task, _ = get_celery_tasks()
            process_workflow_task.delay(job_id)
        
        return render_template('processing.html', job_id=job_id, job=job.to_dict())

    @bp.route('/job-status/<job_id>', methods=['GET'])
    def get_job_status(job_id):
        """Get the current status of a job"""
        try:
            # Get a fresh copy of the job to ensure we have the latest data
            db.session.expire_all()  # Expire all objects in the session
            job = Job.query.get_or_404(job_id)
            
            current_app.logger.info(f"Job status requested for {job_id}: {job.status}")
            current_app.logger.info(f"Current messages count: {len(job.messages) if job.messages else 0}")
            current_app.logger.info(f"Messages content: {job.messages}")
            
            # Ensure messages is always a list
            messages = job.messages if job.messages else []
            
            # Create response data
            response_data = {
                'id': job.id,
                'status': job.status,
                'progress': job.progress,
                'current_phase': job.current_phase,
                'messages': messages,
                'error': job.error,
                'themes': [theme.to_dict() for theme in job.themes] if job.themes else []
            }
            
            current_app.logger.info(f"Returning {len(messages)} messages")
            current_app.logger.info(f"Response data: {response_data}")
            return jsonify(response_data)
        except Exception as e:
            current_app.logger.error(f"Error getting job status: {str(e)}")
            return jsonify({'error': str(e)}), 500

    @bp.route('/results/<job_id>', methods=['GET'])
    def results(job_id):
        try:
            job = Job.query.get_or_404(job_id)
            if job.status != 'completed':
                return redirect(url_for('content_plan.process_job', job_id=job_id))

            # Validate required fields
            if not job.final_plan:
                current_app.logger.error(f"Job {job_id} has no final_plan despite being completed")
                flash("Content plan is not available. Please try again.", "error")
                return redirect(url_for('content_plan.process_job', job_id=job_id))

            plan = job.final_plan
            article_ideas = job.article_ideas or ""
            
            try:
                combined_plan = merge_final_plan_with_articles(plan, article_ideas, FINAL_PLAN_SPLIT_MARKER, PILLAR_TOPICS_HEADING)
            except Exception as e:
                current_app.logger.error(f"Error merging plans for job {job_id}: {str(e)}")
                combined_plan = plan  # Fallback to just the final plan

            job_dict = job.to_dict()
            job_dict['final_plan'] = combined_plan
            
            return render_template('content_plan_result.html', job=job_dict)
        except Exception as e:
            current_app.logger.error(f"Error in results route for job {job_id}: {str(e)}")
            flash("An error occurred while retrieving the content plan. Please try again.", "error")
            return redirect(url_for('content_plan.process_job', job_id=job_id))

    @bp.route('/api/theme-selection/<job_id>', methods=['POST'])
    @csrf.exempt
    def theme_selection(job_id):
        """API route for theme selection
        
        This endpoint receives a theme selection from the client and processes it,
        then starts the next stage of workflow in Celery.
        """
        try:
            # Basic validation - check if job_id is valid
            if not job_id:
                current_app.logger.error("No job_id provided")
                return jsonify({'error': 'No job_id provided'}), 400
                
            # Log request data for debugging
            current_app.logger.info(f"Theme selection request for job {job_id}")
            current_app.logger.info(f"Request content type: {request.content_type}")
            current_app.logger.info(f"Request data: {request.data}")
            
            # Get a fresh copy of the job
            db.session.expire_all()  # Expire all objects in the session
            
            # Check if job exists
            job = Job.query.get(job_id)
            if not job:
                current_app.logger.error(f"Job not found: {job_id}")
                return jsonify({'error': f'Job not found: {job_id}'}), 404
            
            # Check if job is already being processed or not in correct state
            if job.in_progress:
                current_app.logger.warning(f"Theme selection rejected: job {job_id} is already in progress")
                return jsonify({'error': 'Job is already being processed'}), 409
                
            if job.status != 'awaiting_selection':
                # Special case: if job is already 'processing' or 'completed', 
                # return success to avoid frustrating users with refresh/back button
                if job.status in ['processing', 'completed']:
                    current_app.logger.info(f"Job {job_id} status is '{job.status}', returning success for idempotency")
                    return jsonify({
                        'status': 'success',
                        'message': f'Job is already in {job.status} state',
                        'theme': None
                    }), 200
                else:
                    current_app.logger.warning(f"Theme selection rejected: job {job_id} status={job.status}")
                    return jsonify({'error': f'Job is not awaiting theme selection (status={job.status})'}), 409
            
            # Check if we received valid JSON
            if not request.is_json:
                # Try to handle form submission as fallback
                if request.form and 'theme_number' in request.form:
                    theme_number = request.form.get('theme_number')
                    current_app.logger.info(f"Received theme selection via form: {theme_number} for job {job_id}")
                else:
                    current_app.logger.error(f"Received non-JSON request: {request.data}")
                    return jsonify({'error': 'Invalid request format, expected JSON'}), 400
            else:
                data = request.json
                theme_number = data.get('theme_number')
                current_app.logger.info(f"Received theme selection via JSON: {theme_number} for job {job_id}")
            
            # Validate theme number
            if not theme_number:
                return jsonify({'error': 'Theme number not provided'}), 400
                
            # Convert to string if it's not already (to handle different client types)
            if not isinstance(theme_number, str):
                theme_number = str(theme_number)
                
            if not theme_number.isdigit():
                current_app.logger.error(f"Invalid theme number format: {theme_number}")
                return jsonify({'error': 'Invalid theme number format, must be digits only'}), 400
            
            # Idempotency check: if a theme is already selected
            already_selected = Theme.query.filter_by(job_id=job_id, is_selected=True).first()
            if already_selected:
                current_app.logger.warning(f"Theme selection already made for job {job_id}")
                
                # Continue the workflow if not already in progress
                if job.status == 'awaiting_selection':
                    current_app.logger.info(f"Job {job_id} still in awaiting_selection - starting workflow")
                    job.status = 'processing'
                    job.in_progress = False  # Ensure flag is reset
                    job.messages.append(f"Reprocessing selected theme: {already_selected.title}")
                    db.session.commit()
                    
                    # Queue the Celery task
                    try:
                        _, continue_workflow_after_selection_task = get_celery_tasks()
                        task = continue_workflow_after_selection_task.delay(job_id)
                        current_app.logger.info(f"Celery task queued for job {job_id}: {task.id}")
                    except Exception as celery_error:
                        current_app.logger.error(f"Failed to queue Celery task: {str(celery_error)}")
                        # Continue without raising an error since theme selection itself succeeded
                
                return jsonify({
                    'status': 'success', 
                    'message': 'Theme already selected',
                    'theme': already_selected.to_dict()
                }), 200
            
            # Update job with selected theme and advance workflow
            try:
                workflow_manager = WorkflowManager()
                if not job.workflow_data:
                    job.workflow_data = {}
                workflow_manager.load_state(job.workflow_data)
                
                # Find the selected theme
                theme_number = int(theme_number)
                themes = Theme.query.filter_by(job_id=job_id).all()
                
                if not themes or len(themes) == 0:
                    current_app.logger.error(f"No themes found for job {job_id}")
                    return jsonify({'error': 'No themes found for this job'}), 404
                
                if 1 <= theme_number <= len(themes):
                    selected_theme = themes[theme_number-1]
                    selected_theme.is_selected = True
                    db.session.commit()
                else:
                    current_app.logger.error(f"Theme number {theme_number} out of range (1-{len(themes)})")
                    return jsonify({'error': f'Theme number out of range (1-{len(themes)})'}), 400
                
                # Process theme selection
                workflow_manager.process_theme_selection(theme_number, [theme.to_dict() for theme in themes])
                
                # Save updated workflow state
                job.workflow_data = workflow_manager.save_state()
                job.current_phase = workflow_manager.current_phase
                job.messages.append(f"Selected theme: {selected_theme.title}")
                job.status = 'processing'
                job.in_progress = False  # Ensure flag is reset
                db.session.commit()
                
                # Continue workflow in Celery - with better error handling
                try:
                    _, continue_workflow_after_selection_task = get_celery_tasks()
                    task = continue_workflow_after_selection_task.delay(job_id)
                    current_app.logger.info(f"Celery task queued for job {job_id}: {task.id}")
                except Exception as celery_error:
                    # Log the error but don't fail the request
                    current_app.logger.error(f"Failed to queue Celery task: {str(celery_error)}")
                    import traceback
                    current_app.logger.error(traceback.format_exc())
                    # Even if Celery fails, consider the theme selection successful
                    return jsonify({
                        'status': 'success',
                        'message': 'Theme selected but background processing delayed',
                        'theme': selected_theme.to_dict()
                    }), 200
                
                return jsonify({
                    'status': 'success',
                    'message': 'Theme selected',
                    'theme': selected_theme.to_dict()
                }), 200
            except Exception as workflow_error:
                current_app.logger.error(f"Error in workflow processing: {str(workflow_error)}")
                import traceback
                current_app.logger.error(traceback.format_exc())
                return jsonify({'error': f'Error in workflow processing: {str(workflow_error)}'}), 500
        
        except Exception as e:
            current_app.logger.error(f"Error in theme selection: {str(e)}")
            import traceback
            current_app.logger.error(traceback.format_exc())
            return jsonify({'error': f'Server error: {str(e)}'}), 500

    def _scrape_website_content(job):
        """Scrape website content and update job status"""
        job.messages.append(f"Retrieving content from {job.website_url}...")
        website_content = scrape_website(job.website_url)
        
        if website_content.startswith("Error"):
            job.status = 'error'
            job.error = website_content
            job.messages.append(website_content)
            return None
        
        job.website_content_length = len(website_content)
        job.progress = 10
        job.messages.append(f"Retrieved {len(website_content)} characters of content")
        return website_content

    def _search_keywords(job):
        """Search for keywords and update job status"""
        job.messages.append(f"Searching for keywords: {', '.join(job.keywords)}")
        all_search_results = []
        failed_keywords = []
        
        serpapi_key = app.config.get('SERPAPI_API_KEY')
        
        for keyword in job.keywords:
            try:
                results = search_serpapi(keyword, serpapi_key)
                if results:
                    all_search_results.extend(results)
                else:
                    failed_keywords.append(keyword)
                    job.messages.append(f"No results found for keyword: {keyword}")
            except Exception as e:
                failed_keywords.append(keyword)
                job.messages.append(f"Error searching for '{keyword}': {str(e)}")
        
        unique_results = deduplicate_results(all_search_results)
        total_results = len(unique_results)
        
        if total_results == 0:
            job.status = 'error'
            job.error = "No search results were found for any keywords. Try different keywords."
            job.messages.append("No search results were found for any keywords. Try different keywords.")
            return None
        
        job.search_results = unique_results
        job.search_results_count = total_results
        job.progress = 20
        job.messages.append(f"Found {total_results} unique search results after deduplication")
        return unique_results

    def _process_research_phase(job, website_content, search_results):
        """Process the research phase of the workflow"""
        job.messages.append("RESEARCH PHASE: Analyzing website content and search results")
        from utils.agents import run_agent_with_openai
        
        user_message = f"""
        Website URL: {job.website_url}
        Website Content: {website_content}
        Keywords: {', '.join(job.keywords)}
        Search Results: {json.dumps(search_results, indent=2)}
        """
        response = run_agent_with_openai(RESEARCH_AGENT_PROMPT, user_message)
        
        # Parse the results
        brand_brief = ""
        search_analysis = ""
        
        if "## Brand Brief" in response:
            parts = response.split("## Brand Brief", 1)
            if len(parts) > 1:
                remaining = parts[1]
                if "## Search Results Analysis" in remaining:
                    brand_parts = remaining.split("## Search Results Analysis", 1)
                    brand_brief = brand_parts[0].strip()
                    search_analysis = brand_parts[1].strip()
                else:
                    brand_brief = remaining.strip()
        
        job.brand_brief = brand_brief
        job.search_analysis = search_analysis
        job.progress = 40
        job.messages.append("Completed research phase with brand brief and search analysis")
        return brand_brief, search_analysis

    def _process_analysis_phase(job, website_content, brand_brief, search_analysis):
        """Process the analysis phase of the workflow"""
        job.messages.append("ANALYSIS PHASE: Identifying content themes")
        
        user_message = f"""
        Brand Brief: {brand_brief}
        Search Analysis: {search_analysis}
        Website Content: {website_content}
        Keywords: {', '.join(job.keywords)}
        """
        response = run_agent_with_openai(CONTENT_ANALYST_PROMPT, user_message)
        
        # Parse the themes
        themes = []
        if "## Content Themes" in response:
            themes_text = response.split("## Content Themes", 1)[1].strip()
            
            pattern = r'(\d+)\.\s+\*\*(.*?)\*\*\s+(.*?)(?=\d+\.\s+\*\*|\Z)'
            matches = re.finditer(pattern, themes_text, re.DOTALL)
            
            for match in matches:
                theme_num = match.group(1).strip()
                title = match.group(2).strip()
                description = match.group(3).strip()
                
                themes.append({
                    "number": int(theme_num),
                    "title": title,
                    "description": description
                })
        
        job.content_themes = themes
        job.progress = 60
        job.messages.append(f"Identified {len(themes)} content themes")
        return themes

    def process_workflow(job_id):
        """Process the content workflow for a job"""
        job = Job.query.get_or_404(job_id)
        
        try:
            # Step 1: Initialize workflow
            job.status = 'processing'
            job.progress = 0
            
            workflow_manager = WorkflowManager()
            job.workflow_data = workflow_manager.save_state()
            job.current_phase = workflow_manager.current_phase
            
            # Step 2: Scrape website
            website_content = _scrape_website_content(job)
            if not website_content:
                return
            
            # Step 3: Search for keywords
            search_results = _search_keywords(job)
            if not search_results:
                return
            
            # Step 4: Begin agent workflow
            job.messages.append("Starting content research workflow...")
            
            # Advance workflow to RESEARCH phase
            workflow_manager.advance_phase()  # To RESEARCH
            job.workflow_data = workflow_manager.save_state()
            job.current_phase = workflow_manager.current_phase
            
            # Research phase
            brand_brief, search_analysis = _process_research_phase(job, website_content, search_results)
            
            # Advance workflow to ANALYSIS phase
            workflow_manager.advance_phase()  # To ANALYSIS
            job.workflow_data = workflow_manager.save_state()
            job.current_phase = workflow_manager.current_phase
            
            # Analysis phase
            themes = _process_analysis_phase(job, website_content, brand_brief, search_analysis)
            
            # Advance workflow to THEME_SELECTION phase
            workflow_manager.advance_phase()  # To THEME_SELECTION
            job.workflow_data = workflow_manager.save_state()
            job.current_phase = workflow_manager.current_phase
            
            # Wait for user to select a theme
            job.status = 'awaiting_selection'
            job.messages.append("Waiting for user to select a content theme")
            
        except Exception as e:
            job.status = 'error'
            job.error = str(e)
            job.messages.append(f"Error: {str(e)}")
            app.logger.error(f"Error processing job {job_id}: {str(e)}")
            import traceback
            app.logger.error(traceback.format_exc())

    @bp.route('/admin/jobs')
    def admin_jobs():
        # Get all jobs ordered by created_at descending
        jobs = Job.query.order_by(Job.created_at.desc()).all()
        return render_template('admin/jobs.html', jobs=jobs)

    @bp.route('/admin/jobs/cleanup', methods=['POST'])
    def cleanup_jobs():
        try:
            # Start a transaction
            with db.session.begin():
                # First delete themes for incomplete jobs
                db.session.execute(
                    text("""
                    DELETE FROM themes 
                    WHERE job_id IN (
                        SELECT id FROM jobs WHERE status != 'completed'
                    )
                    """)
                )
                
                # Get count of jobs to be deleted
                count = Job.query.filter(Job.status != 'completed').count()
                
                # Then delete the jobs
                db.session.execute(
                    text("DELETE FROM jobs WHERE status != 'completed'")
                )
                
                # Commit the transaction
                db.session.commit()
                
            flash(f'Successfully deleted {count} incomplete jobs and their associated themes', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error deleting jobs: {str(e)}', 'error')
        
        return redirect(url_for('content_plan.admin_jobs'))

    return bp

# Create the blueprint instance
content_plan_bp = create_blueprint()

def init_app(app):
    """Initialize the content plan blueprint with the Flask app"""
    # Initialize SQLAlchemy with Flask app
    db.init_app(app)
    
    # Initialize Celery with Flask app
    from apps.content_plan.celery_config import init_celery
    init_celery(app)
    
    # Initialize Flask-Migrate with the correct migrations directory
    migrate = Migrate(app, db, directory='apps/content_plan/migrations')
    
    # Initialize CSRF protection
    csrf.init_app(app)
    
    # Run migrations on startup
    with app.app_context():
        try:
            from flask_migrate import upgrade
            upgrade()
            app.logger.info("Database migrations completed successfully")
        except Exception as e:
            app.logger.error(f"Error running migrations: {str(e)}")
            raise

