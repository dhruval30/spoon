from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Create the Flask app instance
app = Flask(__name__)

# Add CORS support
CORS(app)

# Import the routes after the app is created to avoid circular imports
from . import routes