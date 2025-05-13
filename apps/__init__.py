from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
from config import Config

db = SQLAlchemy()
migrate = Migrate()

def create_app():
    app = Flask(__name__)
    
    # Configure the app
    app.config.from_object(Config)
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    
    # Register blueprints
    register_blueprints(app)
    
    return app

def register_blueprints(app):
    """Register blueprints with the Flask app"""
    from apps.content_plan.routes import content_plan_bp
    app.register_blueprint(content_plan_bp)
