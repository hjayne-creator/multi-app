import time
import json
from datetime import datetime

class WorkflowManager:
    """Manages the state and progression of the content planning workflow"""
    
    def __init__(self):
        """Initialize the workflow state"""
        # Define the sequential workflow phases
        self.phases = [
            "INITIALIZATION",  # Initial setup
            "RESEARCH",        # ResearchAgent analyzes website and search results
            "ANALYSIS",        # ContentAnalyst identifies themes
            "THEME_SELECTION", # User selects a theme
            "STRATEGY",        # ContentStrategist creates framework
            "CONTENT_IDEATION",# ContentWriter develops article ideas
            "EDITORIAL",       # Editor refines the plan
            "COMPLETION"       # Final wrap-up
        ]
        
        # Start at the initialization phase
        self.current_phase = "INITIALIZATION"
        
        # State variables
        self.waiting_for_user_input = False
        self.selected_theme = None
        self.completed_phases = set()
        
        # Timestamps
        self.phase_timestamps = {
            "INITIALIZATION": datetime.now().isoformat()
        }
        
        # Debug information
        self.transition_history = []
    
    def advance_phase(self):
        """Move to the next phase in the workflow"""
        current_index = self.phases.index(self.current_phase)
        
        # Mark current phase as completed
        self.completed_phases.add(self.current_phase)
        
        # Move to next phase if not at the end
        if current_index < len(self.phases) - 1:
            old_phase = self.current_phase
            self.current_phase = self.phases[current_index + 1]
            
            # Record timestamp
            self.phase_timestamps[self.current_phase] = datetime.now().isoformat()
            
            # Record the transition
            transition = {
                "from": old_phase,
                "to": self.current_phase,
                "timestamp": datetime.now().isoformat()
            }
            self.transition_history.append(transition)
            
            # Print debug info
            print(f"WORKFLOW: Advancing from {old_phase} to {self.current_phase}")
            
            return self.current_phase
        return None
    
    def set_phase(self, phase):
        """Explicitly set the workflow phase"""
        if phase in self.phases:
            old_phase = self.current_phase
            self.current_phase = phase
            
            # Record timestamp
            self.phase_timestamps[self.current_phase] = datetime.now().isoformat()
            
            # Record the transition
            self.transition_history.append({
                "from": old_phase,
                "to": self.current_phase,
                "timestamp": datetime.now().isoformat(),
                "method": "manual_override"
            })
    
    def process_theme_selection(self, theme_number, themes=None):
        """Process a theme selection from the user"""
        # Check if we're already past the THEME_SELECTION phase
        current_index = self.phases.index(self.current_phase)
        theme_selection_index = self.phases.index("THEME_SELECTION")
        
        # If we're already past theme selection, don't change the phase
        skip_advance = current_index > theme_selection_index
        
        if themes and 1 <= theme_number <= len(themes):
            selected = themes[theme_number - 1]
            self.selected_theme = selected
            self.waiting_for_user_input = False
            
            # Print debug info
            print(f"WORKFLOW: Theme selected: {theme_number}, current phase: {self.current_phase}, skip advance: {skip_advance}")
            
            # Advance to the next phase (STRATEGY) only if we're at THEME_SELECTION
            if not skip_advance and self.current_phase == "THEME_SELECTION":
                self.advance_phase()  # From THEME_SELECTION to STRATEGY
            
            return selected
        else:
            # If themes not provided, just store the number
            self.selected_theme = {"number": theme_number, "title": f"Theme {theme_number}"}
            self.waiting_for_user_input = False
            
            # Print debug info
            print(f"WORKFLOW: Theme number stored: {theme_number}, current phase: {self.current_phase}, skip advance: {skip_advance}")
            
            # Advance to the next phase only if we're at THEME_SELECTION
            if not skip_advance and self.current_phase == "THEME_SELECTION":
                self.advance_phase()
            
            return self.selected_theme
    
    def save_state(self):
        """Serialize the workflow state to a dictionary"""
        return {
            "current_phase": self.current_phase,
            "phases": self.phases,
            "waiting_for_user_input": self.waiting_for_user_input,
            "selected_theme": self.selected_theme,
            "completed_phases": list(self.completed_phases),
            "phase_timestamps": self.phase_timestamps,
            "transition_history": self.transition_history
        }
    
    def load_state(self, state_dict):
        """Deserialize workflow state from a dictionary"""
        if not state_dict:
            return
        
        self.current_phase = state_dict.get("current_phase", "INITIALIZATION")
        self.phases = state_dict.get("phases", self.phases)
        self.waiting_for_user_input = state_dict.get("waiting_for_user_input", False)
        self.selected_theme = state_dict.get("selected_theme")
        self.completed_phases = set(state_dict.get("completed_phases", []))
        self.phase_timestamps = state_dict.get("phase_timestamps", {})
        self.transition_history = state_dict.get("transition_history", [])
    
    def visualize_progress(self):
        """Generate a visual representation of workflow progress"""
        progress = []
        for phase in self.phases:
            if phase == self.current_phase:
                progress.append(f"[CURRENT] {phase}")
            elif phase in self.completed_phases:
                progress.append(f"[DONE] {phase}")
            else:
                progress.append(f"[PENDING] {phase}")
        
        return "\n".join(progress)
    
    def get_progress_percentage(self):
        """Calculate progress percentage based on phases completed"""
        total_phases = len(self.phases)
        completed_count = len(self.completed_phases)
        current_index = self.phases.index(self.current_phase)
        
        # Count current phase as half-done if it's not completed yet
        if self.current_phase not in self.completed_phases:
            return int((completed_count + 0.5) / total_phases * 100)
        else:
            return int(completed_count / total_phases * 100)