from datetime import datetime, timezone
import socket
from flask import jsonify, request
from email_sender import send_email
import logging
from flask_sqlalchemy import SQLAlchemy
from flask import Flask, flash, render_template, request, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import sessionmaker
import traceback
import requests
import whois  # Import the 'whois' module for domain expiry check

# Import the SSL checking function
from ssl_checker import check_ssl_expiry  

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql://webmonitor:webmonitor@144.24.8.190/websitemonitoring'
db = SQLAlchemy(app)
engine = db.create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
Session = sessionmaker(bind=engine)
# Set a secret key
app.secret_key = 'your_secret_key_here'

# Define the Website model
class Website(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(255), default="Unknown")  # Updated to string type
    prev_status = db.Column(db.String(255), default="Unknown")  # Added previous status
    ssl_expiry = db.Column(db.DateTime)
    domain_expiry = db.Column(db.DateTime)
    email_notifications = db.Column(db.Boolean, default=False)
    email_notification_email = db.Column(db.String(255))
    telegram_notifications = db.Column(db.Boolean, default=False)
    telegram_notification_phone = db.Column(db.String(20))
    status_history = db.relationship('StatusHistory', backref='website', lazy=True)
    checking_interval = db.Column(db.Integer, default=6)  # Default interval is 60 seconds
    email_sent_up = db.Column(db.Boolean, default=False)  # Flag to track if email for up status has been sent
    email_sent_down = db.Column(db.Boolean, default=False)  # Flag to track if email for down status has been sent

class StatusHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    website_id = db.Column(db.Integer, db.ForeignKey('website.id'), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status_code = db.Column(db.Integer, nullable=False)

    def __init__(self, website_id, status_code):
        self.website_id = website_id
        self.status_code = status_code

# Function to create the tables
def create_tables():
    with app.app_context():
        db.create_all()

# Define routes and views below this lines
@app.route('/')
def index():
    with app.app_context():
        websites = Website.query.all()
        return render_template('index.html', websites=websites)

@app.route('/check_status', methods=['POST'])
def check_status():
    with app.app_context():
        websites = Website.query.all()
        for website in websites:
            try:
                response = requests.get(website.url)
                status_code = response.status_code
                website.prev_status = website.status
                website.status = status_code
                
                if website.prev_status != website.status:
                    # Send email notification for status change
                    if website.email_notifications:
                        if website.status == 200:
                            subject = f"Website {website.name} is back up"
                            body = f"The website {website.name} is now back up. URL: {website.url}"
                            send_email(website.email_notification_email, subject, body)
                            website.email_sent_up = True
                        else:
                            subject = f"Website {website.name} is down"
                            body = f"The website {website.name} is currently down. URL: {website.url}"
                            send_email(website.email_notification_email, subject, body)
                            website.email_sent_down = True
                            
                # Reset email_sent flags when status changes from 200 to non-200 or vice versa
                if website.prev_status == 200 and website.status != 200:
                    website.email_sent_down = False
                elif website.prev_status != 200 and website.status == 200:
                    website.email_sent_up = False

                try:
                    expiry_date = check_ssl_expiry(website.url)
                    website.ssl_expiry = expiry_date
                except Exception as e:
                    logger.error(f"SSL Certificate Error: {e}")

                db.session.commit()
            except requests.RequestException as e:
                website.prev_status = website.status
                website.status = -1

                db.session.commit()

        flash('Status checked for all websites.', 'success')
    return redirect(url_for('index'))

@app.route('/website/add', methods=['GET', 'POST'])
def add_website():
    if request.method == 'POST':
        with app.app_context():
            name = request.form['name']
            url = request.form['url']
            interval = request.form['interval']
            email_notifications = 'email_notifications' in request.form
            email_notification_email = request.form.get('email_notification_email', '')
            # Check if the URL already exists
            existing_website = Website.query.filter_by(url=url).first()
            if existing_website:
                flash('The website is already in the monitoring list.', 'error')
                return redirect(url_for('add_website'))
            
            website = Website(name=name, url=url, checking_interval=int(interval), email_notifications=email_notifications, email_notification_email=email_notification_email)
            db.session.add(website)
            db.session.commit()
            
            # Add the new website to the scheduler
            scheduler.add_job(check_website_status, 'interval', seconds=website.checking_interval, args=[website.id], max_instances=10)
            
            flash('Website added successfully.', 'success')
        return redirect(url_for('index'))
    else:
        return render_template('add_website.html')


@app.route('/website/<int:id>/edit', methods=['GET', 'POST'])
def edit_website(id):
    with app.app_context():
        website = Website.query.get_or_404(id)
        if request.method == 'POST':
            website.name = request.form['name']
            website.url = request.form['url']
            website.checking_interval = int(request.form['interval'])
            website.email_notifications = 'email_notifications' in request.form
            website.email_notification_email = request.form.get('email_notification_email', '')
            db.session.commit()
            flash('Website updated successfully.', 'success')
            return redirect(url_for('index'))
        else:
            return render_template('edit_website.html', website=website)

@app.route('/website/<int:id>/delete', methods=['POST'])
def delete_website(id):
    with app.app_context():
        website = Website.query.get_or_404(id)
        db.session.delete(website)
        db.session.commit()
        flash('Website deleted successfully.', 'success')
        return redirect(url_for('index'))

# Function to check domain expiry
def check_domain_expiry(url):
    try:
        domain_info = whois.whois(url)
        if isinstance(domain_info.expiration_date, list):
            # Return the first expiry date if there are multiple
            expiry_date = domain_info.expiration_date[0]
        else:
            expiry_date = domain_info.expiration_date
        return expiry_date
    except Exception as e:
        print(f"Domain Expiry Error: {e}")
        return None

# Define scheduled task to check website status
def check_website_status(website_id):
    with app.app_context():
        website = Website.query.get(website_id)
        if website:
            try:
                response = requests.get(website.url)
                status_code = response.status_code
                
                # Assign status code to website status
                website.prev_status = website.status
                website.status = status_code
                
                # Check if the status has changed
                if website.prev_status != website.status:
                    # Send email notification for websites that were down and are now up (prev_status not 200 and status 200)
                    if website.email_notifications and website.prev_status != 200 and website.status == 200 and not website.email_sent_up:
                        subject = f"Website {website.name} is back up"
                        body = f"The website {website.name} is now back up. URL: {website.url}"
                        send_email(website.email_notification_email, subject, body)
                        website.email_sent_up = True  # Set flag to indicate email has been sent
                    
                    # Send email notification for websites that are down (prev_status 200 or None/Unknown and status not 200)
                    #if website.email_notifications and (website.prev_status == 200 or website.prev_status is None or website.prev_status == "") and website.status != 200 and not website.email_sent_down:
                    if website.email_notifications and (website.prev_status == 200 or website.prev_status != 200) and website.status != 200 and not website.email_sent_down:
                        subject = f"Website {website.name} is down"
                        body = f"The website {website.name} is currently down. URL: {website.url}"
                        send_email(website.email_notification_email, subject, body)
                        website.email_sent_down = True  # Set flag to indicate email has been sent
                        
                    # Only store non-200 status codes in the status history
                    if status_code != 200:
                        status_history = StatusHistory(website_id=website.id, status_code=status_code)
                        db.session.add(status_history)
                
                # Reset email_sent flags when status changes from 200 to non-200 or vice versa
                if website.prev_status == 200 and website.status != 200 and not website.email_sent_down:
                    website.email_sent_down = False

                elif website.prev_status != 200 and website.status == 200 and not website.email_sent_up:
                    website.email_sent_up = False
                    
            except requests.RequestException as e:
                website.prev_status = website.status
                website.status = -1
                
            website.last_checked = datetime.now(timezone.utc)
            
            # Check SSL certificate expiry
            try:
                expiry_date = check_ssl_expiry(website.url)  # Call function to check SSL certificate expiry
                if expiry_date is not None:
                    website.ssl_expiry = expiry_date
                else:
                    website.ssl_expiry = None  # Update SSL expiry to None if no certificate found
            except Exception as e:
                logger.error(f"SSL Certificate Error: {e}")
                
            # Check domain expiry
            try:
                domain_expiry = check_domain_expiry(website.url)  # Call function to check domain expiry
                website.domain_expiry = domain_expiry
            except Exception as e:
                logger.error(f"Domain Expiry Error: {e}")
                
            db.session.commit()


# Add route to handle sending test email
@app.route('/send_test_email', methods=['POST'])
def send_test_email():
    email = request.json.get('email')
    if email:
        try:
            subject = "Test Email from Website Monitoring System"
            body = "This is a test email sent from the Website Monitoring System. If you received this email, it means the test was successful."
            send_email(email, subject, body)
            app.logger.info(f"Test email sent successfully to {email}.")
            return jsonify({'message': 'Test email sent successfully.'}), 200
        except Exception as e:
            app.logger.error(f"Error sending test email: {e}")
            return jsonify({'error': str(e)}), 500
    else:
        return jsonify({'error': 'Email address not provided.'}), 400

if __name__ == '__main__':
    with app.app_context():
        # Create all database tables if they don't exist
        db.create_all()

        # Create the BackgroundScheduler
        scheduler = BackgroundScheduler()

        # Add the check_website_status job for each website
        for website in Website.query.all():
            scheduler.add_job(check_website_status, 'interval', seconds=website.checking_interval, args=[website.id], max_instances=10)

        # Start the BackgroundScheduler
        scheduler.start()

    # Run the Flask application
    app.run(debug=True)
