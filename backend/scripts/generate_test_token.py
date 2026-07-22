"""
scripts/generate_test_token.py

Utility script to generate a valid JWT token for testing with Postman.
"""

import argparse
import os
import sys

# Load environment variables if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Ensure we can import the app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from app.security.jwt import create_access_token
except ImportError as e:
    print(f"Error importing app modules: {e}")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Generate a JWT token for testing")
    parser.add_argument("--recruiter-id", type=str, required=True, help="The recruiter's UUID or MongoDB ObjectId")
    parser.add_argument("--email", type=str, default="test@example.com", help="The recruiter's email")
    args = parser.parse_args()

    payload = {
        "sub": args.recruiter_id,
        "recruiter_id": args.recruiter_id,
        "email": args.email
    }

    try:
        token = create_access_token(payload)
        print("\n" + "="*50)
        print("YOUR JWT TOKEN FOR POSTMAN:")
        print("="*50)
        print(f"\n{token}\n")
        print("="*50)
        print("How to use in Postman:")
        print("1. Go to the 'Authorization' tab in your request")
        print("2. Select 'Bearer Token' as the Type")
        print("3. Paste the token above into the 'Token' field")
        print("="*50 + "\n")
    except Exception as e:
        print(f"Failed to generate token: {e}")

if __name__ == "__main__":
    main()
