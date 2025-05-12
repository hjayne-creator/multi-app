from flask import Flask
from apps.content_plan.config import get_config
from flask_wtf.csrf import CSRFProtect

def create_app():
    app = Flask(__name__)
    
    # Initialize CSRF protection
    csrf = CSRFProtect()
    csrf.init_app(app)
    
    # Load configuration
    config = get_config()
    app.config.from_object(config)
    config.init_app(app)
    
    # Register blueprints (sub-applications)
    from main.routes import main_bp
    from apps.content_plan.routes import content_plan_bp, init_app as init_content_plan
    from apps.content_gaps.routes import content_gaps_bp
    from apps.topic_competitors.routes import topic_competitors_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(content_plan_bp)
    app.register_blueprint(content_gaps_bp, url_prefix='/apps/content-gaps')
    app.register_blueprint(topic_competitors_bp, url_prefix='/apps/topic-competitors')
    
    # Initialize content plan blueprint
    init_content_plan(app)
    
    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)