from flask import Flask, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
from config import Config
import logging

db = SQLAlchemy()
migrate = Migrate()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    
    # Configure the app
    app.config.from_object(Config)
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    
    # Register blueprints
    register_blueprints(app)
    
    # Register error handlers
    register_error_handlers(app)
    
    return app

def register_blueprints(app):
    """Register blueprints with the Flask app"""
    # Use lazy loading to avoid circular imports
    with app.app_context():
        from apps.content_plan.routes import create_blueprint
        content_plan_bp = create_blueprint()
        app.register_blueprint(content_plan_bp)

def register_error_handlers(app):
    """Register error handlers for common errors"""
    
    @app.errorhandler(500)
    def handle_500_error(e):
        logger.error(f"500 error occurred: {str(e)}")
        return render_template('error.html', 
                             error_code=500,
                             error_message="Internal Server Error",
                             details="The server encountered an internal error. This could be due to a timeout or resource limitation."), 500
    
    @app.errorhandler(502)
    def handle_502_error(e):
        logger.error(f"502 error occurred: {str(e)}")
        return render_template('error.html',
                             error_code=502, 
                             error_message="Bad Gateway",
                             details="The server received an invalid response while processing your request. This may be due to a timeout in the background processing service."), 502
    
    @app.errorhandler(504)
    def handle_504_error(e):
        logger.error(f"504 error occurred: {str(e)}")
        return render_template('error.html',
                             error_code=504,
                             error_message="Gateway Timeout",
                             details="The server did not receive a timely response. This could be due to heavy processing load or network issues."), 504
