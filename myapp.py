import cv2
import langdetect
import ollama
import os
import pdf2image
import pytesseract
import tempfile
from bson import ObjectId
from flask_cors import CORS
from flask import Flask, request, jsonify, Response
from pymongo import MongoClient

# All are allowed
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Connect to MongoDB
client = MongoClient("mongodb://127.0.0.1:27017/")
db = client["Demo_Data"]
collection = db["My_Data"]

# Use the 1st camera
cap = cv2.VideoCapture(0)

# Default model
available_models = [m.model for m in ollama.list().models]
DEFAULT_MODEL = "gemma3:4b"

def select_model(text):
    if "gemma3:4b" in available_models and detect_language(text) == 'th':
        return "gemma3:4b"
    return "granite3.2-vision:latest"

def detect_language(text):
    try:
        return langdetect.detect(text)
    except:
        return "Unknown"

# Scan again
@app.route("/try_again", methods=["POST"])
def try_again():
    data = request.json
    last_entry = collection.find_one(sort=[("_id", -1)])

    if last_entry:
        collection.delete_one({"_id": last_entry["_id"]})

    return jsonify({"message": "Data deleted successfully."}), 200

# Continue scanning
@app.route("/try_more", methods=["POST"])
def try_more():
    data = request.json
    last_entry = collection.find_one(sort=[("_id", -1)])

    if last_entry:
        updated_text = last_entry["text"] + "\n" + data["text"]
        collection.update_one({"_id": last_entry["_id"]}, {"$set": {"text": updated_text}})
    else:
        collection.insert_one({"title": data["title"], "text": data["text"]})

    return jsonify({"message": "Successfully added message."}), 200

# Create new data
@app.route('/create_new', methods=['POST'])
def create_new():
    data = request.get_json()
    text = data.get("text", "")

    last_entry = collection.find_one({"title": {"$regex": "^Scan Text \\d+$"}}, sort=[("_id", -1)])
    if last_entry:
        last_number = int(last_entry["title"].split(" ")[-1])
        new_title = f"Scan Text {last_number + 1}"
    else:

        # New title
        new_title = "Scan Text 01"

    # Save to MongoDB
    new_entry = {"title": new_title, "text": text}
    collection.insert_one(new_entry)

    new_entry["_id"] = str(new_entry["_id"])
    return jsonify({"status": "success", "data": new_entry}), 200

@app.route("/get_history", methods=["GET"])
def get_history():
    data = list(collection.find({}, {"_id": 0}).sort("date", -1))
    return jsonify(data), 200

# Open your camera
@app.route('/open_camera')
def open_camera():
    def generate():
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            _, jpeg = cv2.imencode('.jpg', frame)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n'
                   b'Access-Control-Allow-Origin: *\r\n\r\n' + jpeg.tobytes() + b'\r\n\r\n')

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# Scan image
@app.route('/scan_image', methods=['POST'])
def scan_image():
    ret, frame = cap.read()
    if not ret:
        return jsonify({"error": "Unable to capture image."}), 500

    with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_image:
        cv2.imwrite(temp_image.name, frame)
        temp_image_path = temp_image.name

    # Use default model
    current_model = DEFAULT_MODEL
    response = ollama.chat(
        model=current_model,
        messages=[{
            'role': 'user',
            'content': 'Extract all text from this image.', # Prompt
            'images': [temp_image_path]
        }]
    )

    print(response['message']['content'])
    text = response['message']['content']

    # Select another model
    selected_model = select_model(text)
    if selected_model != DEFAULT_MODEL:
        response = ollama.chat(
            model=selected_model,
            messages=[{
                'role': 'user',
                'content': 'Extract all text from this image.', # Prompt
                'images': [temp_image_path]
            }]
        )

    print(response['message']['content'])
    text = response.get('message', {}).get('content', 'No text found.')

    return jsonify({"text": text}), 200

# Upload file
@app.route("/upload_file", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file."}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower()
    temp_path = f"temp.{ext}"
    file.save(temp_path)
    
    print(f"Received file: {file.filename}, saved as {temp_path}")

    try:
        if ext in ["png", "jpg", "jpeg"]:
            image_path = temp_path
        elif ext == "pdf":
            images = pdf2image.convert_from_path(temp_path)
            images[0].save("temp.jpg", "JPEG")
            image_path = "temp.jpg"
        else:
            return jsonify({"error": "Unsupported file type."}), 400

        response = ollama.chat(
            model="gemma3:4b",
            messages=[{"role": "user", "content": "Extract all text from this image.", "images": [image_path]}] #Prompt
        )

        os.remove(temp_path)
        if ext == "pdf":
            os.remove("temp.jpg")

        print(response['message']['content'])
        return jsonify({"text": response["message"]["content"]}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Delete data
@app.route("/remove_it/<string:title>", methods=["DELETE"])
def remove_it(title):
    result = collection.delete_one({"title": title})

    if result.deleted_count > 0:
        return jsonify({"message": "Delete data successfully."}), 200
    else:
        return jsonify({"message": "Unable to find the data to be deletes."}), 404

# Test server
@app.route('/', methods=['GET'])
def hello():
    return "Hello, World!"

# Post
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
