import os
import psycopg2
from dotenv import load_dotenv

# Connection string
DATABASE_URL = "postgresql://contentplan_db_user:7wXMcHDCmNoHyxRTatHnsqHM7WBevFBD@dpg-d0gji724d50c73ftm4g0-a.oregon-postgres.render.com/contentplan_db"

def add_selected_theme_id_column():
    """Add the selected_theme_id column to the jobs table"""
    print("Connecting to database...")
    
    try:
        # Connect to the database
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        
        # Check if the column already exists
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='jobs' AND column_name='selected_theme_id'
        """)
        
        if cursor.fetchone():
            print("Column 'selected_theme_id' already exists in the jobs table.")
        else:
            # Add the column
            print("Adding 'selected_theme_id' column to jobs table...")
            cursor.execute("""
                ALTER TABLE jobs 
                ADD COLUMN selected_theme_id INTEGER
            """)
            
            # Update the column based on existing theme selections
            print("Updating selected_theme_id based on existing theme selections...")
            cursor.execute("""
                UPDATE jobs j
                SET selected_theme_id = t.id
                FROM themes t
                WHERE t.job_id = j.id AND t.is_selected = TRUE
            """)
            
            conn.commit()
            print("Successfully added and populated 'selected_theme_id' column!")
        
        # Close cursor and connection
        cursor.close()
        conn.close()
        print("Database connection closed.")
        
    except Exception as e:
        print(f"Error updating database: {str(e)}")

if __name__ == "__main__":
    add_selected_theme_id_column() 