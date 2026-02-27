from dotenv import load_dotenv
import os
from mastodon import Mastodon
import fal_client
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
import csv
import random

load_dotenv()

ERROR_FILE = "birdbot_error_time.txt"

# File paths for prompts and other data
HOLIDAYS_FILE = 'holidays.txt'
JOBS_FILE = 'jobs.txt'
PROMPT_FILE = 'prompt.txt'
PROMPT_BASE_FILE = 'prompt_base.txt'

# Mastodon API credentials
MASTODON_BASE_URL = os.getenv('MASTODON_BASE_URL')
MASTODON_ACCESS_TOKEN = os.getenv('MASTODON_ACCESS_TOKEN')

# Fal-AI API credentials
FAL_KEY = os.getenv('FAL_KEY')

# Set up Mastodon API client
print("[INFO] Setting up Mastodon API client...")
mastodon = Mastodon(
    access_token=MASTODON_ACCESS_TOKEN,
    api_base_url=MASTODON_BASE_URL
)

#Grab email address from environment variable
EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS')

def time_until_next_run(target_hour=9):
    """Calculate the time until the next target hour (9 AM by default)."""
    now = datetime.now()
    target_time = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)

    # If it's already past the target time today, schedule for tomorrow
    if now >= target_time:
        target_time += timedelta(days=1)

    return (target_time - now).total_seconds()

def should_retry():
    """Check if the script should retry based on last error time."""
    if os.path.exists(ERROR_FILE):
        with open(ERROR_FILE, 'r') as f:
            content = f.read().strip()
            if not content:
                print("[DEBUG] Error file is empty. Assuming no recent error.")
                return True
            try:
                last_error_time = datetime.fromisoformat(content)
                if datetime.now() < last_error_time + timedelta(hours=24):
                    print("[INFO] Pausing for 24 hours to prevent continuous retries...")
                    return False
            except ValueError:
                print(f"[ERROR] Invalid timestamp in error file: {content}. Ignoring and proceeding.")
                return True
    return True

def record_error():
    """Record the current time as the last error time."""
    with open(ERROR_FILE, 'w') as f:
        f.write(datetime.now().isoformat())

def send_email(subject: str, body: str, image_bytes: bytes, to_email: str):
    """Send an email with the image attached using msmtp."""
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
            print(f"[ERROR] Failed to send email: {stderr.decode()}")
        else:
            print(f"[INFO] Email sent successfully to {to_email}")
    except Exception as e:
        print(f"[ERROR] An error occurred while sending the email: {e}")

def generate_image(prompt: str) -> bytes:
    """Generate an image using fal.ai FLUX 2 Pro model from the given prompt."""
    print(f"[INFO] Generating image with prompt: {prompt}")
    result = fal_client.subscribe(
    "fal-ai/flux-2-pro",
    arguments={
        "prompt": prompt,
        "image_size": "square_hd"
    }
)

    image_url = result['images'][0]['url']
    print(f"[INFO] Downloading image from {image_url}")
    r = requests.get(image_url)
    r.raise_for_status()
    return r.content

def post_image_to_mastodon(image_bytes: bytes, status_text: str, alt_text: str):
    """Post the generated image to Mastodon with alt text and status."""
    print("[INFO] Uploading image to Mastodon...")
    media = mastodon.media_post(media_file=BytesIO(image_bytes), mime_type="image/png", description=alt_text)
    print("[INFO] Image uploaded. Posting status...")
    mastodon.status_post(status=status_text, media_ids=[media['id']])
    print("[INFO] Status posted successfully.")

def get_random_job():
    if not os.path.exists(JOBS_FILE):
        print(f"[ERROR] Jobs file '{JOBS_FILE}' not found.")
        sys.exit(1)
    with open(JOBS_FILE, 'r') as f:
        jobs = [line.strip() for line in f if line.strip()]
    return random.choice(jobs)

def generate_prompt():
    """Generate a prompt by combining the base prompt with a random job and any matching holiday."""
    today = datetime.now().strftime('%-m/%-d/%Y')
    print(f"[INFO] Today's date: {today}")

    if not os.path.exists(PROMPT_BASE_FILE):
        print(f"[ERROR] Base prompt file '{PROMPT_BASE_FILE}' not found.")
        sys.exit(1)

    with open(PROMPT_BASE_FILE, 'r') as f:
        base_prompt = f.read().strip()
    print(f"[INFO] Base prompt read from '{PROMPT_BASE_FILE}'")

    # Job substitution
    job = get_random_job()
    base_prompt = base_prompt.replace('{job}', job)
    print(f"[INFO] Random job selected: {job}")

    # Holiday substitution
    holiday_name = None
    holiday_text = ''
    if os.path.exists(HOLIDAYS_FILE):
        matches = []
        with open(HOLIDAYS_FILE, 'r', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['Date'] == today:
                    matches.append(row)

        if matches:
            chosen = random.choice(matches)
            print(f"[INFO] Chosen holiday: {chosen}")
            holiday_name = chosen["Name"]
            holiday_text = (
                f' It is also "{holiday_name}" ({chosen["Type"].lower()}), '
                f'so the workplace is decorated accordingly and the goose '
                f'is wearing a small festive accessory.'
            )

    base_prompt = base_prompt.replace('{holiday}', holiday_text)
    print(f"[INFO] Final prompt: {base_prompt}")

    with open(PROMPT_FILE, 'w') as f:
        f.write(base_prompt + '\n')

    return base_prompt, holiday_name

def main_loop():
    """Main loop that runs the bot, checking for errors and waiting until 9 AM between runs."""
    while True:

        # Check if we should retry based on the last error
        if not should_retry():
            time.sleep(3600)  # Check again in 1 hour if still within the error wait period
            continue

        try:
            print("[INFO] Starting a new loop iteration...")

            # Generate the dynamic prompt before creating the image
            image_prompt, holiday_name = generate_prompt()

            # Generate image using the prompt
            image_bytes = generate_image(image_prompt)

            # Build the Mastodon status message
            if holiday_name:
                status_text = f"Here's a random silly goose celebrating \"{holiday_name}\" - generated by AI!\n#bird #birds #goose #geese #ai #nature"
                email_body_text = f"Here's a random silly goose celebrating \"{holiday_name}\" - generated by AI!"
            else:
                status_text = "Here's a random silly goose - generated by AI!\n#bird #birds #goose #geese #ai #nature"
                email_body_text = "Here's a random silly goose - generated by AI!"

            # Post image to Mastodon
            post_image_to_mastodon(
                image_bytes,
                status_text,
                "Here's a random silly goose - generated by AI!"
            )

            # Send image via email
            send_email(
                subject="Your Silly Goose Image",
                body=email_body_text,
                image_bytes=image_bytes,
                to_email=EMAIL_ADDRESS
            )

        except Exception as e:
            print(f"[ERROR] An unexpected error occurred: {e}. Retrying in 10 minutes...")
            record_error()
            time.sleep(600)

        # Wait until 9 AM before the next iteration
        seconds_until_9am = time_until_next_run(9)
        print(f"[INFO] Waiting until 9 AM. Sleeping for {seconds_until_9am} seconds...")
        time.sleep(seconds_until_9am)

if __name__ == "__main__":
    main_loop()
