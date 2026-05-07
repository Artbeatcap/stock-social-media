from flask import render_template, url_for
import os
from flask_mail import Message
from app import mail, app
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail as SGMail, Email as SGEmail, To as SGTo, Content as SGContent
import logging

logger = logging.getLogger(__name__)

def render_morning_brief(context: dict) -> tuple[str, str]:
    """
    Render morning brief email templates with the given context.
    
    Args:
        context: Dictionary containing all template variables
        
    Returns:
        Tuple of (html_content, text_content)
    """
    try:
        # Add default values for optional fields
        context.setdefault('sectors', '')
        context.setdefault('extra_levels', '')
        context.setdefault('on_deck', '')
        context.setdefault('unsubscribe_url', '#')
        context.setdefault('preferences_url', '#')
        
        # Create a minimal Flask app context for template rendering
        from flask import Flask
        from jinja2 import Environment, FileSystemLoader
        import os
        
        # Create a minimal Flask app for template rendering
        template_app = Flask(__name__)
        template_app.template_folder = 'templates'
        
        with template_app.app_context():
            # Render HTML template
            html_content = render_template('email/morning_brief.html.jinja', **context)
            
            # Render text template
            text_content = render_template('email/morning_brief.txt.jinja', **context)
            
            return html_content, text_content
        
    except Exception as e:
        logger.error(f"Error rendering morning brief templates: {str(e)}")
        raise

def send_morning_brief(html: str, text: str, subject: str, to_list: list[str]) -> bool:
    """
    Send morning brief email using configured mail provider.
    
    Args:
        html: HTML email content
        text: Plain text email content
        subject: Email subject line
        to_list: List of recipient email addresses
        
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        # Get email configuration
        from_name = os.getenv('EMAIL_FROM_NAME', 'Options Plunge')
        from_email = os.getenv('EMAIL_FROM', 'support@optionsplunge.com')
        mail_provider = os.getenv('MAIL_PROVIDER', 'sendgrid')
        
        # Prefer SendGrid if configured
        if mail_provider == 'sendgrid':
            sendgrid_key = app.config.get('SENDGRID_KEY') or os.getenv('SENDGRID_API_KEY')
            if sendgrid_key:
                try:
                    sg = SendGridAPIClient(api_key=sendgrid_key)
                    from_email_obj = SGEmail(from_email, from_name)
                    
                    # Send to each recipient
                    for recipient in to_list:
                        to_email = SGTo(recipient)
                        content_html = SGContent("text/html", html)
                        content_text = SGContent("text/plain", text)
                        
                        sg_mail = SGMail(from_email_obj, to_email, subject, content_text)
                        sg_mail.add_content(content_html)
                        
                        response = sg.send(sg_mail)
                        if response.status_code not in (200, 202):
                            logger.error(f"SendGrid error for {recipient}: {response.status_code}")
                            return False
                        
                        logger.info(f"Morning brief sent via SendGrid to {recipient}")
                    
                    return True
                    
                except Exception as sg_err:
                    logger.warning(f"SendGrid failed: {sg_err}. Falling back to SMTP.")
                    # Fall through to SMTP
        
        # Fallback to Flask-Mail SMTP
        msg = Message(
            subject,
            recipients=to_list,
            html=html,
            body=text,
            sender=(from_name, from_email)
        )
        mail.send(msg)
        logger.info(f"Morning brief sent via SMTP to {len(to_list)} recipients")
        return True
        
    except Exception as e:
        logger.error(f"Error sending morning brief: {str(e)}")
        return False

def send_morning_brief_direct(html: str, text: str, subject: str, to_list: list[str]) -> bool:
    """
    Send morning brief email using direct SendGrid (no Flask context).
    
    Args:
        html: HTML email content
        text: Plain text email content
        subject: Email subject line
        to_list: List of recipient email addresses
        
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        # Get SendGrid key (support both var names)
        sendgrid_key = os.getenv('SENDGRID_KEY') or os.getenv('SENDGRID_API_KEY')
        if not sendgrid_key:
            logger.error("SENDGRID_KEY or SENDGRID_API_KEY not configured")
            return False
        
        # Get email configuration
        from_name = os.getenv('EMAIL_FROM_NAME', 'Options Plunge')
        from_email = os.getenv('EMAIL_FROM', 'support@optionsplunge.com')
        
        sg = SendGridAPIClient(api_key=sendgrid_key)
        from_email_obj = SGEmail(from_email, from_name)
        
        # Send to each recipient
        for recipient in to_list:
            to_email = SGTo(recipient)
            content_html = SGContent("text/html", html)
            content_text = SGContent("text/plain", text)
            
            sg_mail = SGMail(from_email_obj, to_email, subject, content_text)
            sg_mail.add_content(content_html)
            
            response = sg.send(sg_mail)
            if response.status_code not in (200, 202):
                logger.error(f"SendGrid error for {recipient}: {response.status_code}")
                return False
            
            logger.info(f"Morning brief sent via SendGrid to {recipient}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error sending morning brief: {str(e)}")
        return False

def render_weekly_brief(context: dict) -> tuple[str, str]:
    """
    Render weekly brief email templates with the given context.
    
    Args:
        context: Dictionary containing all template variables
        
    Returns:
        Tuple of (html_content, text_content)
    """
    try:
        # Add default values for optional fields
        context.setdefault('unsubscribe_url', '#')
        context.setdefault('preferences_url', '#')
        
        # Create a minimal Flask app context for template rendering
        from flask import Flask
        from jinja2 import Environment, FileSystemLoader
        import os
        
        # Create a minimal Flask app for template rendering
        template_app = Flask(__name__)
        template_app.template_folder = 'templates'
        
        with template_app.app_context():
            # Render HTML template
            html_content = render_template('email/weekly_brief.html.jinja', **context)
            
            # Render text template
            text_content = render_template('email/weekly_brief.txt.jinja', **context)
            
            return html_content, text_content
        
    except Exception as e:
        logger.error(f"Error rendering weekly brief templates: {str(e)}")
        raise

def send_weekly_brief(html: str, text: str, subject: str, to_list: list[str]) -> bool:
    """
    Send weekly brief email using configured mail provider.
    
    Args:
        html: HTML email content
        text: Plain text email content
        subject: Email subject line
        to_list: List of recipient email addresses
        
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        # Get email configuration
        from_name = os.getenv('EMAIL_FROM_NAME', 'Options Plunge')
        from_email = os.getenv('EMAIL_FROM', 'support@optionsplunge.com')
        mail_provider = os.getenv('MAIL_PROVIDER', 'sendgrid')
        
        # Prefer SendGrid if configured
        if mail_provider == 'sendgrid':
            sendgrid_key = app.config.get('SENDGRID_KEY') or os.getenv('SENDGRID_API_KEY')
            if sendgrid_key:
                try:
                    sg = SendGridAPIClient(api_key=sendgrid_key)
                    from_email_obj = SGEmail(from_email, from_name)
                    
                    # Send to each recipient
                    for recipient in to_list:
                        to_email = SGTo(recipient)
                        content_html = SGContent("text/html", html)
                        content_text = SGContent("text/plain", text)
                        
                        sg_mail = SGMail(from_email_obj, to_email, subject, content_text)
                        sg_mail.add_content(content_html)
                        
                        response = sg.send(sg_mail)
                        if response.status_code not in (200, 202):
                            logger.error(f"SendGrid error for {recipient}: {response.status_code}")
                            return False
                        
                        logger.info(f"Weekly brief sent via SendGrid to {recipient}")
                    
                    return True
                    
                except Exception as sg_err:
                    logger.warning(f"SendGrid failed: {sg_err}. Falling back to SMTP.")
                    # Fall through to SMTP
        
        # Fallback to Flask-Mail SMTP
        msg = Message(
            subject,
            recipients=to_list,
            html=html,
            body=text,
            sender=(from_name, from_email)
        )
        mail.send(msg)
        logger.info(f"Weekly brief sent via SMTP to {len(to_list)} recipients")
        return True
        
    except Exception as e:
        logger.error(f"Error sending weekly brief: {str(e)}")
        return False

def send_weekly_brief_direct(html: str, text: str, subject: str, to_list: list[str]) -> bool:
    """
    Send weekly brief email using direct SendGrid (no Flask context).
    
    Args:
        html: HTML email content
        text: Plain text email content
        subject: Email subject line
        to_list: List of recipient email addresses
        
    Returns:
        True if sent successfully, False otherwise
    """
    try:
        # Get SendGrid key (support both var names)
        sendgrid_key = os.getenv('SENDGRID_KEY') or os.getenv('SENDGRID_API_KEY')
        if not sendgrid_key:
            logger.error("SENDGRID_KEY or SENDGRID_API_KEY not configured")
            return False
        
        # Get email configuration
        from_name = os.getenv('EMAIL_FROM_NAME', 'Options Plunge')
        from_email = os.getenv('EMAIL_FROM', 'support@optionsplunge.com')
        
        sg = SendGridAPIClient(api_key=sendgrid_key)
        from_email_obj = SGEmail(from_email, from_name)
        
        # Send to each recipient
        for recipient in to_list:
            to_email = SGTo(recipient)
            content_html = SGContent("text/html", html)
            content_text = SGContent("text/plain", text)
            
            sg_mail = SGMail(from_email_obj, to_email, subject, content_text)
            sg_mail.add_content(content_html)
            
            response = sg.send(sg_mail)
            if response.status_code not in (200, 202):
                logger.error(f"SendGrid error for {recipient}: {response.status_code}")
                return False
            
            logger.info(f"Weekly brief sent via SendGrid to {recipient}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error sending weekly brief: {str(e)}")
        return False
