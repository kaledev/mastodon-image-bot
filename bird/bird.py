import os
from mastodon import Mastodon
import openai
from openai import OpenAI
import requests
from io import BytesIO
import time
import subprocess
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import sys
import base64
from datetime import datetime, timedelta

ERROR_FILE = "birdbot_error_time.txt"

# Mastodon API credentials
MASTODON_BASE_URL = os.getenv('"MASTODON_BASE_URL')
MASTODON_ACCESS_TOKEN = os.getenv('MASTODON_ACCESS_TOKEN')

# OpenAI API credentials
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Set up Mastodon API client
print("Setting up Mastodon API client...")
mastodon = Mastodon(
    access_token=MASTODON_ACCESS_TOKEN,
    api_base_url=MASTODON_BASE_URL
)

#Grab email address from environment variable
EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS')

# Set up OpenAI API client
print("Setting up OpenAI API client...")
client = OpenAI(api_key=OPENAI_API_KEY)

def time_until_next_run(target_hour=9):
    """Calculate the time until the next target hour (9 AM by default)."""
    now = datetime.now()
    target_time = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)

    # If it's already past the target time today, schedule for tomorrow
    if now >= target_time:
        target_time += timedelta(days=1)

    return (target_time - now).total_seconds()

def should_retry():
    if os.path.exists(ERROR_FILE):
        with open(ERROR_FILE, 'r') as f:
            content = f.read().strip()
            if not content:
                print("Error file is empty. Assuming no recent error.")
                return True
            try:
                last_error_time = datetime.fromisoformat(content)
                if datetime.now() < last_error_time + timedelta(hours=24):
                    print("Pausing for 24 hours to prevent continuous retries...")
                    return False
            except ValueError:
                print(f"Invalid timestamp in error file: {content}. Ignoring and proceeding.")
                return True
    return True

def record_error():
    with open(ERROR_FILE, 'w') as f:
        f.write(datetime.now().isoformat())

def send_email(subject: str, body: str, image_bytes: bytes, to_email: str):
    # Create the email message
    msg = MIMEMultipart('related')
    msg['To'] = to_email
    msg['Subject'] = subject

    # Create a plain text part with a reference to the inline image
    text_body = f"{body}\n\nImage is attached."
    msg.attach(MIMEText(text_body, 'plain'))

    # Create a MIME part for the image
    image_part = MIMEBase('image', 'png')
    image_part.set_payload(image_bytes)
    encoders.encode_base64(image_part)
    image_part.add_header('Content-ID', '<image1>')
    image_part.add_header('Content-Disposition', 'inline; filename="image.png"')

    # Attach the image to the message
    msg.attach(image_part)

    # Send the email using msmtp
    try:
        process = subprocess.Popen(
            ["/usr/bin/msmtp", "--debug", "--from=default", to_email],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate(msg.as_string().encode('utf-8'))

        if process.returncode != 0:
            print(f"Failed to send email: {stderr.decode()}")
        else:
            print(f"Email sent successfully to {to_email}")
    except Exception as e:
        print(f"An error occurred while sending the email: {e}")

def generate_image(prompt: str) -> bytes:
    print(f"Generating image with prompt: {prompt}")
    response = client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1024",
        quality="standard",
        n=1,
    )
    image_url = response.data[0].url
    print(f"Image generated successfully. Downloading from {image_url}...")
    image_response = requests.get(image_url)
    image_response.raise_for_status()
    print("Image downloaded successfully.")
    return image_response.content

def post_image_to_mastodon(image_bytes: bytes, status_text: str, alt_text: str):
    print("Uploading image to Mastodon...")
    media = mastodon.media_post(media_file=BytesIO(image_bytes), mime_type="image/png", description=alt_text)
    print("Image uploaded successfully. Posting status...")
    mastodon.status_post(status=status_text, media_ids=[media['id']])
    print("Status posted successfully.")

def load_prompt_from_file(file_path):
    try:
        with open(file_path, 'r') as file:
            prompt = file.read().strip()
            return prompt
    except Exception as e:
        print(f"Error loading prompt from file: {e}")
        return None

def main_loop():
    prompt_file = "prompt.txt"

    while True:

        # Check if we should retry based on the last error
        if not should_retry():
            time.sleep(3600)  # Check again in 1 hour if still within the error wait period
            continue

        try:
            print("Starting a new loop iteration...")
            # Load the prompt and generate the image as before
            image_prompt = load_prompt_from_file(prompt_file)
            if image_prompt is None:
                print("No valid prompt found. Skipping this iteration.")
                time.sleep(60)
                continue

            image_bytes = generate_image(image_prompt)
            post_image_to_mastodon(image_bytes, "Here's a random floofy-headed bird - generated by AI!\n#floofy #bird #birds #ai #nature","Here's a random floofy-headed bird - generated by AI!")
            send_email(
                subject="Your Floofy-Headed Bird Image",
                body="Here is the floofy-headed bird image generated by AI.",
                image_bytes=image_bytes,
                to_email=EMAIL_ADDRESS
            )
        except Exception as e:
            print(f"An unexpected error occurred: {e}. Retrying in 10 minutes...")
            record_error()
            time.sleep(600)

        # Wait until 9 AM before the next iteration
        seconds_until_9am = time_until_next_run(9)
        print(f"Waiting until 9 AM. Sleeping for {seconds_until_9am} seconds...")
        time.sleep(seconds_until_9am)


if __name__ == "__main__":
    main_loop()
