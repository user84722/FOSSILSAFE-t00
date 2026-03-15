"""
SMTP email notification provider.
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, Tuple, Optional
from backend.notifications.base import NotificationProvider


class SMTPNotifier(NotificationProvider):
    """Email notification provider using SMTP."""
    
    def send(self, event_type: str, data: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Send email notification."""
        if not self.is_enabled():
            return False, "SMTP notifier is disabled"
        
        try:
            # Get configuration
            smtp_server = self.config.get('smtp_server')
            smtp_port = self.config.get('smtp_port', 587)
            username = self.config.get('username')
            password = self.config.get('password')
            from_addr = self.config.get('from_address', username)
            to_addr = self.config.get('to_address')
            use_tls = self.config.get('use_tls', True)
            
            if not all([smtp_server, username, password, to_addr]):
                return False, "SMTP configuration incomplete"
            
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = self.format_event_title(event_type, data)
            msg['From'] = from_addr
            msg['To'] = to_addr
            
            # Plain text version
            text_body = self.format_event_message(event_type, data)
            text_part = MIMEText(text_body, 'plain')
            msg.attach(text_part)
            
            # HTML version
            html_body = self._format_html_email(event_type, data)
            html_part = MIMEText(html_body, 'html')
            msg.attach(html_part)
            
            # Send email
            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                if use_tls:
                    server.starttls()
                server.login(username, password)
                server.send_message(msg)
            
            return True, None
            
        except smtplib.SMTPAuthenticationError:
            return False, "SMTP authentication failed"
        except smtplib.SMTPException as e:
            return False, f"SMTP error: {str(e)}"
        except Exception as e:
            return False, f"Failed to send email: {str(e)}"
    
    def test_connection(self) -> Tuple[bool, str]:
        """Test SMTP connection."""
        try:
            smtp_server = self.config.get('smtp_server')
            smtp_port = self.config.get('smtp_port', 587)
            username = self.config.get('username')
            password = self.config.get('password')
            use_tls = self.config.get('use_tls', True)
            
            if not all([smtp_server, username, password]):
                return False, "SMTP configuration incomplete"
            
            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                if use_tls:
                    server.starttls()
                server.login(username, password)
            
            return True, "SMTP connection successful"
            
        except smtplib.SMTPAuthenticationError:
            return False, "SMTP authentication failed - check username/password"
        except smtplib.SMTPException as e:
            return False, f"SMTP error: {str(e)}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def _format_html_email(self, event_type: str, data: Dict[str, Any]) -> str:
        """Format HTML email body."""
        title = self.format_event_title(event_type, data)
        
        # Determine color based on event type
        if 'failed' in event_type:
            color = '#dc2626'  # red
        elif 'completed' in event_type:
            color = '#16a34a'  # green
        elif 'started' in event_type:
            color = '#2563eb'  # blue
        else:
            color = '#6b7280'  # gray
        
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: {color}; color: white; padding: 20px; border-radius: 5px 5px 0 0; }}
                .content {{ background-color: #f9fafb; padding: 20px; border-radius: 0 0 5px 5px; }}
                .detail {{ margin: 10px 0; }}
                .label {{ font-weight: bold; color: #4b5563; }}
                .footer {{ margin-top: 20px; padding-top: 20px; border-top: 1px solid #e5e7eb; color: #6b7280; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2 style="margin: 0;">{title}</h2>
                </div>
                <div class="content">
        """
        
        # Add details
        if 'job_name' in data:
            html += f'<div class="detail"><span class="label">Job:</span> {data["job_name"]}</div>'
        if 'status' in data:
            html += f'<div class="detail"><span class="label">Status:</span> {data["status"]}</div>'
        if 'error' in data:
            html += f'<div class="detail"><span class="label">Error:</span> {data["error"]}</div>'
        if 'duration' in data:
            html += f'<div class="detail"><span class="label">Duration:</span> {data["duration"]}</div>'
        if 'tape_barcode' in data:
            html += f'<div class="detail"><span class="label">Tape:</span> {data["tape_barcode"]}</div>'
        if 'timestamp' in data:
            html += f'<div class="detail"><span class="label">Time:</span> {data["timestamp"]}</div>'
        
        html += """
                    <div class="footer">
                        This is an automated notification from FossilSafe LTO Backup System.
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
