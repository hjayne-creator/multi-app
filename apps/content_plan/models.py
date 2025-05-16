from datetime import datetime
from sqlalchemy.dialects.postgresql import JSON
from apps import db

class Job(db.Model):
    __tablename__ = 'jobs'

    id = db.Column(db.String(36), primary_key=True)  # UUID
    status = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    website_url = db.Column(db.String(500), nullable=False)
    keywords = db.Column(JSON, nullable=False)
    current_phase = db.Column(db.String(50), nullable=False)
    progress = db.Column(db.Integer, default=0)
    workflow_data = db.Column(JSON)
    messages = db.Column(JSON, default=list)  # Ensure default is a list
    error = db.Column(db.Text)
    website_content_length = db.Column(db.Integer)
    search_results = db.Column(JSON)
    search_results_count = db.Column(db.Integer)
    brand_brief = db.Column(db.Text)
    search_analysis = db.Column(db.Text)
    content_cluster = db.Column(db.Text)
    article_ideas = db.Column(db.Text)
    final_plan = db.Column(db.Text)
    completed_at = db.Column(db.DateTime)
    themes = db.relationship('Theme', back_populates='job', cascade='all, delete-orphan')
    in_progress = db.Column(db.Boolean, default=False)
    selected_theme_id = db.Column(db.Integer)  # Store the ID of the selected theme

    def __init__(self, **kwargs):
        super(Job, self).__init__(**kwargs)
        if self.messages is None:
            self.messages = []

    def to_dict(self):
        return {
            'id': self.id,
            'status': self.status,
            'created_at': self.created_at.isoformat(),
            'website_url': self.website_url,
            'keywords': self.keywords,
            'current_phase': self.current_phase,
            'progress': self.progress,
            'workflow_data': self.workflow_data,
            'messages': self.messages,
            'error': self.error,
            'website_content_length': self.website_content_length,
            'search_results': self.search_results,
            'search_results_count': self.search_results_count,
            'brand_brief': self.brand_brief,
            'search_analysis': self.search_analysis,
            'content_cluster': self.content_cluster,
            'article_ideas': self.article_ideas,
            'final_plan': self.final_plan,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'themes': [theme.to_dict() for theme in self.themes]
        }

class Theme(db.Model):
    __tablename__ = 'themes'

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(36), db.ForeignKey('jobs.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    keywords = db.Column(JSON)
    is_selected = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    job = db.relationship('Job', back_populates='themes')

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'keywords': self.keywords,
            'is_selected': self.is_selected,
            'created_at': self.created_at.isoformat()
        } 