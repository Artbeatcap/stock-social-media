from flask import render_template, url_for
import os
from flask_mail import Message
from app import mail, app
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail as SGMail,
    Email as SGEmail,
    To as SGTo,
    Content as SGContent,
    TrackingSettings,
    ClickTracking,
)
from models import MarketBriefSubscriber, User
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def send_confirmation_email(subscriber):
    """Send confirmation email to new subscriber"""
    try:
        # Generate confirmation token if not already set
        if not subscriber.token:
            subscriber.generate_confirmation_token()
            from app import db
            db.session.commit()
        
        # Create confirmation URL using url_for with external=True for full domain
        confirm_url = url_for('confirm_subscription',
                             token=subscriber.token,
                             _external=True,           # full https://optionsplunge.com/confirm/<token>
                             _scheme='https')          # force https in production
        
        html = render_template('email/confirm_brief.html', name=subscriber.name, url=confirm_url)

        # Prefer SendGrid if configured, fallback to Flask-Mail
        sendgrid_key = app.config.get('SENDGRID_KEY') or os.getenv('SENDGRID_KEY')
        if sendgrid_key:
            try:
                sg = SendGridAPIClient(api_key=sendgrid_key)
                from_email_name, from_email_addr = app.config.get('MAIL_DEFAULT_SENDER', (None, None))
                from_email = SGEmail(from_email_addr or 'support@optionsplunge.com', from_email_name or 'Options Plunge Support')
                to_email = SGTo(subscriber.email)
                subject = 'Confirm your Morning Market Brief subscription'
                content = SGContent("text/html", html)
                sg_mail = SGMail(from_email, to_email, subject, content)
                response = sg.send(sg_mail)
                if response.status_code not in (200, 202):
                    raise RuntimeError(f"SendGrid non-2xx status: {response.status_code}")
                logger.info(f"Confirmation email sent via SendGrid to {subscriber.email}")
            except Exception as sg_err:
                logger.warning(f"SendGrid failed for {subscriber.email}: {sg_err}. Falling back to SMTP.")
                msg = Message('Confirm your Morning Market Brief subscription',
                              recipients=[subscriber.email], html=html,
                              sender=app.config['MAIL_DEFAULT_SENDER'])
                mail.send(msg)
                logger.info(f"Confirmation email sent via SMTP to {subscriber.email}")
        else:
            msg = Message('Confirm your Morning Market Brief subscription',
                          recipients=[subscriber.email], html=html,
                          sender=app.config['MAIL_DEFAULT_SENDER'])
            mail.send(msg)
            logger.info(f"Confirmation email sent to {subscriber.email}")
        return True
        
    except Exception as e:
        logger.error(f"Error sending confirmation email to {subscriber.email}: {str(e)}")
        return False

def send_confirmation_email_direct(subscriber):
    """Send confirmation email using direct SendGrid (no Flask context)"""
    try:
        # Generate confirmation token if not already set
        if not subscriber.token:
            subscriber.generate_confirmation_token()
            from app import db
            db.session.commit()
        
        # Get SendGrid key
        sendgrid_key = os.getenv('SENDGRID_KEY')
        if not sendgrid_key:
            logger.error("SENDGRID_KEY not configured")
            return False
        
        # Create confirmation URL directly (no Flask url_for)
        # Use optionsplunge.com as the default domain for production
        server_name = os.getenv('SERVER_NAME', 'optionsplunge.com')
        scheme = os.getenv('PREFERRED_URL_SCHEME', 'https')
        confirm_url = f"{scheme}://{server_name}/confirm/{subscriber.token}"
        
        # Create email HTML directly (no Flask render_template)
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Confirm Your Subscription</title>
        </head>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <h1 style="color: #2c3e50;">Confirm Your Subscription</h1>
                <p>Hi {subscriber.name},</p>
                <p>Thank you for subscribing to the Morning Market Brief!</p>
                <p>Please click the button below to confirm your subscription:</p>
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{confirm_url}" style="background-color: #3498db; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">Confirm Subscription</a>
                </div>
                <p>Or copy and paste this link into your browser:</p>
                <p style="word-break: break-all; color: #666;">{confirm_url}</p>
                <p>If you didn't request this subscription, you can safely ignore this email.</p>
                <p>Best regards,<br>Options Plunge Team</p>
            </div>
        </body>
        </html>
        """
        
        # Send via SendGrid (direct approach like stock news email)
        sg = SendGridAPIClient(api_key=sendgrid_key)
        from_email = SGEmail("support@optionsplunge.com", "Options Plunge Support")
        to_email = SGTo(subscriber.email)
        subject = 'Confirm your Morning Market Brief subscription'
        content = SGContent("text/html", html_content)
        sg_mail = SGMail(from_email, to_email, subject, content)
        
        response = sg.send(sg_mail)
        if response.status_code in (200, 202):
            logger.info(f"Confirmation email sent via SendGrid to {subscriber.email}")
            return True
        else:
            logger.error(f"SendGrid error: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending confirmation email to {subscriber.email}: {str(e)}")
        return False

def send_daily_brief_to_subscribers(brief_html, date_str=None):
    """Send daily brief only to Pro users (active or trialing) who opted into daily emails"""
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    # Select only Pro users who opted in to daily brief
    subscribers = User.query.filter(
        User.is_subscribed_daily == True,
        User.subscription_status.in_(['active', 'trialing'])
    ).all()
    
    if not subscribers:
        logger.info("No confirmed subscribers found")
        return 0
    
    success_count = 0
    
    for subscriber in subscribers:
        try:
            # Create unsubscribe and preferences URLs
            domain = app.config.get('SERVER_NAME', 'optionsplunge.com')
            scheme = app.config.get('PREFERRED_URL_SCHEME', 'https')
            unsubscribe_url = f"{scheme}://{domain}/unsubscribe/{subscriber.email}"
            preferences_url = f"{scheme}://{domain}/market_brief"
            
            # Render the email with the brief content
            email_html = render_template('email/daily_brief.html',
                                       content=brief_html,
                                       date=date_str,
                                       unsubscribe_url=unsubscribe_url,
                                       preferences_url=preferences_url)
            
            sendgrid_key = app.config.get('SENDGRID_KEY') or os.getenv('SENDGRID_KEY')
            if sendgrid_key:
                try:
                    sg = SendGridAPIClient(api_key=sendgrid_key)
                    from_email_name, from_email_addr = app.config.get('MAIL_DEFAULT_SENDER', (None, None))
                    from_email = SGEmail(from_email_addr or 'support@optionsplunge.com', from_email_name or 'Options Plunge Support')
                    to_email = SGTo(subscriber.email)
                    subject = f'Morning Market Brief - {date_str}'
                    content = SGContent("text/html", email_html)
                    sg_mail = SGMail(from_email, to_email, subject, content)
                    resp = sg.send(sg_mail)
                    if resp.status_code not in (200, 202):
                        raise RuntimeError(f"SendGrid non-2xx status: {resp.status_code}")
                except Exception as sg_err:
                    logger.warning(f"SendGrid failed for {subscriber.email}: {sg_err}. Falling back to SMTP.")
                    msg = Message(f'Morning Market Brief - {date_str}',
                                  recipients=[subscriber.email], html=email_html,
                                  sender=app.config['MAIL_DEFAULT_SENDER'])
                    mail.send(msg)
            else:
                msg = Message(f'Morning Market Brief - {date_str}',
                              recipients=[subscriber.email], html=email_html,
                              sender=app.config['MAIL_DEFAULT_SENDER'])
                mail.send(msg)
            
            # For User model we do not update MarketBriefSubscriber.last_brief_sent here
            # Consider persisting via EmailDelivery separately
            
            success_count += 1
            logger.info(f"Daily brief sent to {subscriber.email}")
            
        except Exception as e:
            logger.error(f"Error sending daily brief to {subscriber.email}: {str(e)}")
            continue
    
    logger.info(f"Daily brief sent to {success_count}/{len(subscribers)} Pro users")
    return success_count

def send_daily_brief_direct(brief_html, date_str=None):
    """Send daily brief to Pro users using direct SendGrid (no Flask context)"""
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    # Import here to avoid circular imports
    from app import app, db
    from models import User
    
    # Use Flask app context for database queries
    with app.app_context():
        # Select only Pro users who opted in to daily brief
        subscribers = User.query.filter(
            User.is_subscribed_daily == True,
            User.subscription_status.in_(['active', 'trialing'])
        ).all()
        
        if not subscribers:
            logger.info("No confirmed subscribers found")
            return 0
        
        # Prefer SendGrid if configured; otherwise fall back to Flask-Mail SMTP
        sendgrid_key = os.getenv('SENDGRID_KEY')
        success_count = 0

        for subscriber in subscribers:
            try:
                # Create URLs directly (no Flask context)
                server_name = os.getenv('SERVER_NAME', 'optionsplunge.com')
                scheme = os.getenv('PREFERRED_URL_SCHEME', 'https')
                unsubscribe_url = f"{scheme}://{server_name}/unsubscribe/{subscriber.email}"
                preferences_url = f"{scheme}://{server_name}/market_brief"
                
                # Create email HTML directly
                email_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="utf-8">
                    <title>Morning Market Brief</title>
                </head>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                        {brief_html}
                        <hr style="margin: 30px 0; border: none; border-top: 1px solid #eee;">
                        <p style="text-align: center; color: #666; font-size: 12px;">
                            <a href="{unsubscribe_url}">Unsubscribe</a> | 
                            <a href="{preferences_url}">Preferences</a>
                        </p>
                    </div>
                </body>
                </html>
                """
                
                sent_via_sendgrid = False
                if sendgrid_key:
                    try:
                        # Send via SendGrid (direct approach)
                        sg = SendGridAPIClient(api_key=sendgrid_key)
                        from_email = SGEmail("support@optionsplunge.com", "Options Plunge Support")
                        to_email = SGTo(subscriber.email)
                        subject = f'Morning Market Brief - {date_str}'
                        content = SGContent("text/html", email_html)
                        sg_mail = SGMail(from_email, to_email, subject, content)

                        response = sg.send(sg_mail)
                        if response.status_code in (200, 202):
                            success_count += 1
                            sent_via_sendgrid = True
                            logger.info(f"Daily brief sent to {subscriber.email} via SendGrid")
                        else:
                            logger.warning(
                                f"SendGrid non-success status {response.status_code} for {subscriber.email}; will fallback to SMTP"
                            )
                    except Exception as sg_err:
                        logger.warning(f"SendGrid failed for {subscriber.email}: {sg_err}; will fallback to SMTP")

                if not sent_via_sendgrid:
                    # Fallback to SMTP using Flask-Mail
                    try:
                        from flask_mail import Message, Mail
                        mail = Mail(app)
                        msg = Message(
                            subject=f'Morning Market Brief - {date_str}',
                            recipients=[subscriber.email],
                            html=email_html,
                            sender=app.config.get('MAIL_DEFAULT_SENDER')
                        )
                        mail.send(msg)
                        success_count += 1
                        logger.info(f"Daily brief sent to {subscriber.email} via SMTP fallback")
                    except Exception as smtp_err:
                        logger.error(f"SMTP fallback failed for {subscriber.email}: {smtp_err}")
                    
            except Exception as e:
                logger.error(f"Error sending daily brief to {subscriber.email}: {str(e)}")
                continue
        
        logger.info(f"Daily brief sent to {success_count}/{len(subscribers)} Pro users")
        return success_count

def send_welcome_email(subscriber):
    """Send welcome email after confirmation"""
    try:
        html = render_template('email/welcome_brief.html', name=subscriber.name)

        # Prefer SendGrid if configured; fallback to SMTP
        sendgrid_key = app.config.get('SENDGRID_KEY') or os.getenv('SENDGRID_KEY')
        if sendgrid_key:
            try:
                sg = SendGridAPIClient(api_key=sendgrid_key)
                from_name, from_addr = app.config.get('MAIL_DEFAULT_SENDER', (None, None))
                from_email = SGEmail((from_addr or os.getenv('EMAIL_FROM') or 'no-reply@send.optionsplunge.com'), (from_name or 'Options Plunge'))
                to_email = SGTo(subscriber.email)
                subject = 'Welcome to the Morning Market Brief!'
                content = SGContent('text/html', html)
                sg_mail = SGMail(from_email, to_email, subject, content)
                # Disable click tracking for clarity on links
                ts = TrackingSettings()
                ts.click_tracking = ClickTracking(False, False)
                sg_mail.tracking_settings = ts
                resp = sg.send(sg_mail)
                if resp.status_code not in (200, 202):
                    raise RuntimeError(f"SendGrid non-2xx status: {resp.status_code}")
                logger.info(f"Welcome email sent via SendGrid to {subscriber.email}")
                return True
            except Exception as sg_err:
                logger.warning(f"SendGrid failed for welcome {subscriber.email}: {sg_err}. Falling back to SMTP.")

        msg = Message(
            'Welcome to the Morning Market Brief!',
            recipients=[subscriber.email],
            html=html,
            sender=app.config['MAIL_DEFAULT_SENDER']
        )
        mail.send(msg)
        logger.info(f"Welcome email sent via SMTP to {subscriber.email}")
        return True
        
    except Exception as e:
        logger.error(f"Error sending welcome email to {subscriber.email}: {str(e)}")
        return False


def send_welcome_on_register(user):
    """Send a welcome confirmation on account registration.
    - Free users: confirm weekly brief subscription; invite to upgrade for daily
    - Pro users: confirm daily + weekly delivery
    """
    try:
        name = getattr(user, 'display_name', None) or getattr(user, 'username', '') or 'Trader'
        # Build pricing URL with safe fallback outside request context
        pricing_url = '#'
        # Hardcode public pricing URL to avoid any tracking/redirect issues
        pricing_url = 'https://optionsplunge.com/pricing'
        try:
            logger.info(f"welcome_on_register pricing_url: {pricing_url}")
        except Exception:
            pass

        ctx = {
            'name': name,
            'daily': bool(getattr(user, 'is_subscribed_daily', False)),
            'weekly': bool(getattr(user, 'is_subscribed_weekly', True)),
            'pricing_url': pricing_url
        }

        html = render_template('email/welcome_on_register.html', **ctx)
        try:
            logger.info(f"welcome_on_register rendered href: {ctx.get('pricing_url')}")
        except Exception:
            pass

        # Prefer SendGrid if configured; fallback to SMTP
        sendgrid_key = app.config.get('SENDGRID_KEY') or os.getenv('SENDGRID_KEY')
        if sendgrid_key:
            try:
                sg = SendGridAPIClient(api_key=sendgrid_key)
                from_name, from_addr = app.config.get('MAIL_DEFAULT_SENDER', (None, None))
                from_email = SGEmail((from_addr or os.getenv('EMAIL_FROM') or 'no-reply@send.optionsplunge.com'), (from_name or 'Options Plunge'))
                to_email = SGTo(user.email)
                subject = 'Welcome to Options Plunge — Market Brief Preferences'
                content = SGContent('text/html', html)
                sg_mail = SGMail(from_email, to_email, subject, content)
                # Disable click tracking so the button shows optionsplunge.com link directly
                ts = TrackingSettings()
                ts.click_tracking = ClickTracking(False, False)
                sg_mail.tracking_settings = ts
                resp = sg.send(sg_mail)
                if resp.status_code not in (200, 202):
                    raise RuntimeError(f"SendGrid non-2xx status: {resp.status_code}")
                logger.info(f"Welcome-on-register email sent to {user.email}")
                return True
            except Exception as sg_err:
                logger.warning(f"SendGrid failed for welcome-on-register {user.email}: {sg_err}. Falling back to SMTP.")

        msg = Message(
            'Welcome to Options Plunge — Market Brief Preferences',
            recipients=[user.email],
            html=html,
            sender=app.config['MAIL_DEFAULT_SENDER']
        )
        mail.send(msg)
        logger.info(f"Welcome-on-register email sent to {user.email}")
        return True
    except Exception as e:
        logger.error(f"Error sending welcome-on-register to {getattr(user,'email', '<unknown>')}: {str(e)}")
        return False

def send_admin_notification(subscriber):
    """Notify admin of new subscription"""
    try:
        admin_email = app.config.get('ADMIN_EMAIL')
        if not admin_email:
            return False
            
        msg = Message(
            'New Market Brief Subscriber',
            recipients=[admin_email],
            body=f"""
New subscriber to Morning Market Brief:
Name: {subscriber.name}
Email: {subscriber.email}
Date: {subscriber.subscribed_at.strftime('%Y-%m-%d %H:%M:%S')}
            """.strip(),
            sender=app.config['MAIL_DEFAULT_SENDER']
        )
        
        mail.send(msg)
        logger.info(f"Admin notification sent for {subscriber.email}")
        return True
        
    except Exception as e:
        logger.error(f"Error sending admin notification: {str(e)}")
        return False 


def send_verification_email(user, token):
    """Send email verification email with compelling subject"""
    try:
        # Create verification URL
        verify_url = url_for('verify_email', token=token, _external=True)
        
        # Compelling subject line
        subject = "🎯 Unlock Your Options Plunge Account - Verify Your Email"
        
        # HTML body
        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; text-align: center; color: white;">
                <h1 style="margin: 0;">Welcome to Options Plunge!</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">You're one step away from better trading</p>
            </div>
            
            <div style="padding: 30px; background: #f8f9fa;">
                <p>Hi {user.username},</p>
                
                <p>Thanks for signing up! Click the button below to verify your email address and unlock all features:</p>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{verify_url}" style="background: linear-gradient(135deg, #27ae60, #2ecc71); color: white; padding: 15px 40px; text-decoration: none; border-radius: 25px; font-weight: bold; display: inline-block;">
                        Verify My Email
                    </a>
                </div>
                
                <p><strong>What you'll unlock:</strong></p>
                <ul style="line-height: 1.8;">
                    <li>📊 Track unlimited trades</li>
                    <li>📝 Maintain your trading journal</li>
                    <li>📈 View performance analytics</li>
                    <li>📧 Receive weekly market briefs</li>
                    <li>⭐ Upgrade to Pro for AI-powered insights</li>
                </ul>
                
                <p style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #dee2e6; color: #7f8c8d; font-size: 0.9rem;">
                    <strong>Link not working?</strong> Copy and paste this URL into your browser:<br>
                    <a href="{verify_url}" style="color: #3498db; word-break: break-all;">{verify_url}</a>
                </p>
                
                <p style="color: #7f8c8d; font-size: 0.9rem;">
                    This link expires in 24 hours for security. If you didn't create this account, you can safely ignore this email.
                </p>
            </div>
            
            <div style="padding: 20px; text-align: center; color: #95a5a6; font-size: 0.85rem;">
                <p>Questions? Reply to this email or contact us at support@optionsplunge.com</p>
                <p>&copy; 2025 Options Plunge. All rights reserved.</p>
            </div>
        </div>
        """
        
        # Text body (fallback)
        text_body = f"""
        Welcome to Options Plunge!
        
        Hi {user.username},
        
        Thanks for signing up! Please verify your email address by clicking the link below:
        
        {verify_url}
        
        What you'll unlock:
        - Track unlimited trades
        - Maintain your trading journal
        - View performance analytics
        - Receive weekly market briefs
        - Upgrade to Pro for AI-powered insights
        
        This link expires in 24 hours for security.
        
        If you didn't create this account, you can safely ignore this email.
        
        Questions? Contact us at support@optionsplunge.com
        
        - The Options Plunge Team
        """
        
        # Send via SendGrid
        sendgrid_key = app.config.get('SENDGRID_KEY') or os.getenv('SENDGRID_KEY')
        if sendgrid_key:
            try:
                sg = SendGridAPIClient(api_key=sendgrid_key)
                from_email_name, from_email_addr = app.config.get('MAIL_DEFAULT_SENDER', (None, None))
                from_email = SGEmail(from_email_addr or 'support@optionsplunge.com', from_email_name or 'Options Plunge Support')
                to_email = SGTo(user.email)
                content = SGContent("text/html", html_body)
                sg_mail = SGMail(from_email, to_email, subject, content)
                response = sg.send(sg_mail)
                if response.status_code not in (200, 202):
                    raise RuntimeError(f"SendGrid non-2xx status: {response.status_code}")
                logger.info(f"Verification email sent via SendGrid to {user.email}")
                return True
            except Exception as sg_err:
                logger.warning(f"SendGrid failed for {user.email}: {sg_err}. Falling back to SMTP.")
                msg = Message(subject,
                              recipients=[user.email], 
                              html=html_body,
                              body=text_body,
                              sender=app.config['MAIL_DEFAULT_SENDER'])
                mail.send(msg)
                logger.info(f"Verification email sent via SMTP to {user.email}")
                return True
        else:
            msg = Message(subject,
                          recipients=[user.email], 
                          html=html_body,
                          body=text_body,
                          sender=app.config['MAIL_DEFAULT_SENDER'])
            mail.send(msg)
            logger.info(f"Verification email sent to {user.email}")
            return True
        
    except Exception as e:
        logger.error(f"Error sending verification email to {user.email}: {str(e)}")
        return False