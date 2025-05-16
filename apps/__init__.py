from flask import Flask, render_template, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
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
    
    # Enable CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
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
    
    def is_api_request():
        """Check if the current request is for an API endpoint"""
        path = request.path
        return '/api/' in path
    
    @app.errorhandler(400)
    def handle_400_error(e):
        logger.error(f"400 error occurred: {str(e)}")
        if is_api_request():
            return jsonify({
                "error": "Bad Request",
                "status": 400,
                "details": str(e)
            }), 400
        return render_template('error.html', 
                             error_code=400,
                             error_message="Bad Request",
                             details="The server could not understand your request."), 400
    
    @app.errorhandler(404)
    def handle_404_error(e):
        logger.error(f"404 error occurred: {str(e)}")
        if is_api_request():
            return jsonify({
                "error": "Not Found",
                "status": 404,
                "details": str(e)
            }), 404
        return render_template('error.html', 
                             error_code=404,
                             error_message="Not Found",
                             details="The requested resource could not be found."), 404
    
    @app.errorhandler(405)
    def handle_405_error(e):
        logger.error(f"405 error occurred: {str(e)}")
        if is_api_request():
            return jsonify({
                "error": "Method Not Allowed",
                "status": 405,
                "details": str(e)
            }), 405
        return render_template('error.html', 
                             error_code=405,
                             error_message="Method Not Allowed",
                             details="The method is not allowed for the requested URL."), 405
    
    @app.errorhandler(500)
    def handle_500_error(e):
        logger.error(f"500 error occurred: {str(e)}")
        if is_api_request():
            # Return JSON for API requests
            return jsonify({
                "error": "Internal Server Error",
                "status": 500,
                "details": str(e)
            }), 500
        # Return HTML for regular requests
        return render_template('error.html', 
                             error_code=500,
                             error_message="Internal Server Error",
                             details="The server encountered an internal error. This could be due to a timeout or resource limitation."), 500
    
    @app.errorhandler(502)
    def handle_502_error(e):
        logger.error(f"502 error occurred: {str(e)}")
        if is_api_request():
            # Return JSON for API requests
            return jsonify({
                "error": "Bad Gateway",
                "status": 502,
                "details": str(e)
            }), 502
        # Return HTML for regular requests
        return render_template('error.html',
                             error_code=502, 
                             error_message="Bad Gateway",
                             details="The server received an invalid response while processing your request. This may be due to a timeout in the background processing service."), 502
    
    @app.errorhandler(504)
    def handle_504_error(e):
        logger.error(f"504 error occurred: {str(e)}")
        if is_api_request():
            # Return JSON for API requests
            return jsonify({
                "error": "Gateway Timeout",
                "status": 504,
                "details": str(e)
            }), 504
        # Return HTML for regular requests
        return render_template('error.html',
                             error_code=504,
                             error_message="Gateway Timeout",
                             details="The server did not receive a timely response. This could be due to heavy processing load or network issues."), 504
