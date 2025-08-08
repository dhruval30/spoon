from app import app
import os

if __name__ == '__main__':
    # The port can be set in the .env file or default to 5000
    port = int(os.environ.get("PORT", 5000))
    # debug=True is great for development
    app.run(debug=True, host="0.0.0.0", port=port)