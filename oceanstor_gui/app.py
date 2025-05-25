from flask import Flask, jsonify, request, render_template
import boto3
import uuid
import json

# TODO: Configure these placeholder variables with your OceanStor S3 details
OCEANSTOR_ENDPOINT_URL = "YOUR_OCEANSTOR_S3_ENDPOINT_URL"  # e.g., "http://192.168.1.100:8080"
OCEANSTOR_ACCESS_KEY_ID = "YOUR_OCEANSTOR_ACCESS_KEY"
OCEANSTOR_SECRET_ACCESS_KEY = "YOUR_OCEANSTOR_SECRET_KEY"
OCEANSTOR_REGION_NAME = "your-region"  # e.g., "us-east-1" or specific region if applicable

app = Flask(__name__)

# In-memory store for bucket credentials (for demonstration purposes)
# In a production environment, use a secure way to store and manage credentials.
bucket_credentials_store = {}

# In-memory store for IAM policies (for demonstration purposes)
iam_policies_store = {}

# In-memory store for bucket policy assignments
bucket_policy_assignments = {}

def get_s3_client():
    """Initializes and returns a boto3 S3 client."""
    return boto3.client(
        's3',
        aws_access_key_id=OCEANSTOR_ACCESS_KEY_ID,
        aws_secret_access_key=OCEANSTOR_SECRET_ACCESS_KEY,
        endpoint_url=OCEANSTOR_ENDPOINT_URL,
        region_name=OCEANSTOR_REGION_NAME,
        # It's good practice to explicitly disable SSL verification if using self-signed certs
        # in a non-production/test environment, but be cautious.
        # verify=False # Uncomment if using self-signed certificates and understand the risks
    )

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200

@app.route('/api/buckets', methods=['POST'])
def create_bucket():
    data = request.get_json()
    if not data or 'bucket_name' not in data:
        return jsonify({"error": "Missing bucket_name in request"}), 400

    bucket_name = data['bucket_name']

    # Basic validation for bucket name (can be more sophisticated)
    if not bucket_name:
        return jsonify({"error": "bucket_name cannot be empty"}), 400

    try:
        s3_client = get_s3_client()
        # Attempt to create the bucket
        # Note: Actual bucket creation on some S3-compatible storages might require
        # specific configurations or might be restricted.
        # For Huawei OceanStor, ensure the underlying storage pool and policies are set up.
        s3_client.create_bucket(Bucket=bucket_name)

        # Generate unique credentials for this bucket
        # For simplicity, we're using UUIDs. In a real system, you'd integrate
        # with an IAM or user management system.
        generated_access_key = uuid.uuid4().hex
        generated_secret_key = uuid.uuid4().hex

        bucket_credentials_store[bucket_name] = {
            "access_key": generated_access_key,
            "secret_key": generated_secret_key
        }

        return jsonify({
            "bucket_name": bucket_name,
            "access_key": generated_access_key,
            "secret_key": generated_secret_key,
            "message": "Bucket created successfully (simulated) and credentials generated."
        }), 201

    except Exception as e:
        # Basic error handling. In a real app, log more details.
        # Common boto3 errors: ClientError, NoCredentialsError, EndpointConnectionError
        # Specific error for bucket already exists: ClientError with error code 'BucketAlreadyOwnedByYou' or similar
        error_message = str(e)
        if "BucketAlreadyOwnedByYou" in error_message or "BucketAlreadyExists" in error_message :
             return jsonify({"error": f"Bucket '{bucket_name}' already exists.", "details": error_message}), 409 # Conflict
        return jsonify({"error": f"Failed to create bucket '{bucket_name}'.", "details": error_message}), 500

@app.route('/api/buckets/<string:bucket_name>/credentials', methods=['GET'])
def get_bucket_credentials(bucket_name):
    if bucket_name in bucket_credentials_store:
        return jsonify(bucket_credentials_store[bucket_name]), 200
    else:
        return jsonify({"error": "Credentials not found for this bucket or bucket does not exist."}), 404

@app.route('/api/buckets/<string:bucket_name>/policy', methods=['PUT'])
def assign_bucket_policy(bucket_name):
    data = request.get_json()
    if not data or 'policy_name' not in data:
        return jsonify({"error": "Missing policy_name in request"}), 400

    policy_name = data['policy_name']

    if policy_name not in iam_policies_store:
        return jsonify({"error": f"Policy '{policy_name}' not found"}), 404

    if bucket_name not in bucket_credentials_store: # Check if bucket was 'created' by our app
        return jsonify({"error": f"Bucket '{bucket_name}' not found or not managed by this application"}), 404

    policy_document = iam_policies_store[policy_name]

    try:
        s3_client = get_s3_client()
        s3_client.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy_document))
        
        bucket_policy_assignments[bucket_name] = policy_name
        
        return jsonify({
            "message": f"Policy '{policy_name}' applied to bucket '{bucket_name}' successfully"
        }), 200
    except Exception as e:
        # In a real app, inspect 'e' for specific boto3 errors (e.g., ClientError)
        error_message = str(e)
        return jsonify({
            "error": f"Failed to apply policy '{policy_name}' to bucket '{bucket_name}'.",
            "details": error_message
        }), 500

@app.route('/api/buckets/<string:bucket_name>/policy', methods=['GET'])
def get_bucket_policy(bucket_name):
    if bucket_name not in bucket_credentials_store: # Check if bucket is known
         return jsonify({"error": f"Bucket '{bucket_name}' not found or not managed by this application"}), 404

    assigned_policy_name = bucket_policy_assignments.get(bucket_name)
    
    if assigned_policy_name:
        return jsonify({
            "bucket_name": bucket_name,
            "policy_name": assigned_policy_name,
            "policy_document": iam_policies_store.get(assigned_policy_name) # Also return the document for clarity
        }), 200
    else:
        return jsonify({"message": "No policy assigned to this bucket"}), 404

# --- IAM Policy Management Endpoints ---

@app.route('/api/iam/policies', methods=['POST'])
def create_iam_policy():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    policy_name = data.get('policy_name')
    policy_document = data.get('policy_document') # Assuming policy_document is the key for the policy content

    if not policy_name or not isinstance(policy_name, str):
        return jsonify({"error": "policy_name is required and must be a string"}), 400
    
    if not policy_document or not isinstance(policy_document, dict): # Basic check for policy document
        return jsonify({"error": "policy_document is required and must be a JSON object"}), 400

    if policy_name in iam_policies_store:
        return jsonify({"error": f"Policy '{policy_name}' already exists. Use PUT to update."}), 409 # Conflict

    iam_policies_store[policy_name] = policy_document
    return jsonify({"message": f"Policy '{policy_name}' stored successfully"}), 201

@app.route('/api/iam/policies', methods=['GET'])
def get_iam_policies():
    return jsonify(iam_policies_store), 200

@app.route('/api/iam/policies/<string:policy_name>', methods=['GET'])
def get_iam_policy(policy_name):
    policy = iam_policies_store.get(policy_name)
    if policy:
        return jsonify(policy), 200
    else:
        return jsonify({"error": f"Policy '{policy_name}' not found"}), 404

# --- Frontend Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/iam')
def iam_page():
    return render_template('iam.html')

if __name__ == '__main__':
    # This part is for local testing and might not be directly runnable
    # by the agent in the current environment, but it's good practice to include.
    # Ensure Flask is installed: pip install Flask boto3
    print("Starting Flask app. Ensure placeholder S3 configurations are updated if connecting to a real OceanStor S3.")
    print(f"OCEANSTOR_ENDPOINT_URL: {OCEANSTOR_ENDPOINT_URL}")
    print(f"OCEANSTOR_ACCESS_KEY_ID: {'*' * len(OCEANSTOR_ACCESS_KEY_ID) if OCEANSTOR_ACCESS_KEY_ID != 'YOUR_OCEANSTOR_ACCESS_KEY' else OCEANSTOR_ACCESS_KEY_ID}")
    print(f"OCEANSTOR_SECRET_ACCESS_KEY: {'*' * len(OCEANSTOR_SECRET_ACCESS_KEY) if OCEANSTOR_SECRET_ACCESS_KEY != 'YOUR_OCEANSTOR_SECRET_KEY' else OCEANSTOR_SECRET_ACCESS_KEY}")
    app.run(debug=True, host='0.0.0.0', port=5000)
