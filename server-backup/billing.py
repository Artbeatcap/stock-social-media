import os
from flask import Blueprint, request, jsonify, current_app, url_for, render_template, redirect
from flask_login import current_user, login_required
from models import db, User

# Import stripe after other imports to avoid circular import
try:
    import stripe
except ImportError:
    stripe = None

bp = Blueprint("billing", __name__, url_prefix="/api/billing")

# Initialize Stripe
if stripe:
    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Price IDs from environment variables
PRICE_MONTHLY = os.getenv("STRIPE_PRICE_MONTHLY")   # e.g., price_123
PRICE_ANNUAL = os.getenv("STRIPE_PRICE_ANNUAL")    # e.g., price_456

def requires_pro(f):
    """Decorator to require Pro subscription"""
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "Authentication required"}), 401
        if not current_user.has_pro_access():
            return jsonify({"error": "Pro subscription required"}), 402
        return f(*args, **kwargs)
    return wrapper

@bp.route("/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    """Create a Stripe Checkout Session for subscription"""
    if not stripe:
        return jsonify({"error": "Stripe not available"}), 500
        
    data = request.get_json(force=True)
    plan = data.get("plan", "monthly")
    
    if plan not in ["monthly", "annual"]:
        return jsonify({"error": "Invalid plan type"}), 400
    
    price_id = PRICE_MONTHLY if plan == "monthly" else PRICE_ANNUAL
    
    if not price_id:
        current_app.logger.error(f"Stripe price ID not configured for plan: {plan}")
        return jsonify({"error": "Pricing not configured"}), 500

    try:
        # Create or get Stripe customer
        customer_id = current_user.stripe_customer_id
        if not customer_id:
            customer = stripe.Customer.create(
                email=current_user.email,
                metadata={"user_id": current_user.id}
            )
            current_user.stripe_customer_id = customer.id
            db.session.commit()
            customer_id = customer.id

        # Prepare subscription data with trial if eligible
        subscription_data = {}
        if not current_user.had_trial:
            subscription_data["trial_period_days"] = 14

        # Create checkout session
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            payment_method_types=["card"],
            payment_method_collection="always",  # collect card now
            line_items=[{"price": price_id, "quantity": 1}],
            # Important: avoid url_for encoding the curly braces so Stripe can replace them
            success_url=(request.host_url.rstrip('/') + url_for('billing.success') + "?session_id={CHECKOUT_SESSION_ID}"),
            cancel_url=request.host_url.rstrip('/') + url_for('billing.pricing'),
            subscription_data=subscription_data,
            metadata={
                "user_id": current_user.id,
                "plan_type": plan
            },
            allow_promotion_codes=True,
        )
        
        return jsonify({"checkout_url": session.url})
        
    except Exception as e:
        current_app.logger.exception("Stripe checkout error")
        return jsonify({"error": str(e)}), 400

@bp.route("/webhook", methods=["POST"])
def webhook_received():
    """Handle Stripe webhooks"""
    if not stripe:
        return jsonify({"error": "Stripe not available"}), 500
        
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        current_app.logger.error(f"Webhook signature verification failed: {e}")
        return str(e), 400

    # Handle the event
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        handle_checkout_completed(session)
    elif event["type"] == "customer.subscription.updated":
        subscription = event["data"]["object"]
        handle_subscription_updated(subscription)
    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        handle_subscription_canceled(subscription)
    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        handle_payment_failed(invoice)
    else:
        current_app.logger.info(f"Unhandled event type: {event['type']}")

    return "", 200

def handle_checkout_completed(session):
    """Handle successful checkout completion"""
    try:
        user_id = int(session.metadata.get("user_id"))
        plan_type = session.metadata.get("plan_type", "monthly")
        
        user = User.query.get(user_id)
        if not user:
            current_app.logger.error(f"User not found for checkout: {user_id}")
            return
        
        # Get subscription details to check for trial
        subscription = stripe.Subscription.retrieve(session.subscription)
        
        # Update user subscription status
        user.subscription_status = subscription.status  # 'trialing' or 'active'
        user.plan_type = plan_type
        user.stripe_customer_id = session.customer
        user.stripe_subscription_id = session.subscription
        # Enable daily + weekly brief for Pro
        try:
            user.is_subscribed_daily = True
            user.is_subscribed_weekly = True
        except Exception:
            pass
        
        # Set trial end date if trial exists
        if subscription.get("trial_end"):
            from datetime import datetime
            user.trial_end = datetime.utcfromtimestamp(subscription["trial_end"])
            user.had_trial = True
        
        db.session.commit()
        current_app.logger.info(f"User {user_id} subscription activated: {plan_type}, status: {subscription.status}")
        
    except Exception as e:
        current_app.logger.exception("Error handling checkout completion")

def handle_subscription_updated(subscription):
    """Handle subscription updates"""
    try:
        user = User.query.filter_by(stripe_subscription_id=subscription.id).first()
        if not user:
            current_app.logger.error(f"User not found for subscription: {subscription.id}")
            return
        
        # Update subscription status
        user.subscription_status = subscription.status
        # Keep brief subscriptions aligned with Pro status
        try:
            if subscription.status in ("active", "trialing"):
                user.is_subscribed_daily = True
                user.is_subscribed_weekly = True
            elif subscription.status in ("canceled", "past_due", "unpaid", "incomplete_expired"):
                # Keep weekly for free users, disable daily
                user.is_subscribed_daily = False
                user.is_subscribed_weekly = True
        except Exception:
            pass
        
        # Update trial end date if present
        if subscription.get("trial_end"):
            from datetime import datetime
            user.trial_end = datetime.utcfromtimestamp(subscription["trial_end"])
        elif subscription.status == "active" and user.trial_end:
            # Trial ended, clear trial_end
            user.trial_end = None
        
        db.session.commit()
        current_app.logger.info(f"User {user.id} subscription updated: {subscription.status}")
        
    except Exception as e:
        current_app.logger.exception("Error handling subscription update")

def handle_subscription_canceled(subscription):
    """Handle subscription cancellation"""
    try:
        user = User.query.filter_by(stripe_subscription_id=subscription.id).first()
        if not user:
            current_app.logger.error(f"User not found for canceled subscription: {subscription.id}")
            return
        
        user.subscription_status = "canceled"
        user.trial_end = None  # Clear trial end on cancellation
        # On cancel, disable daily brief but keep weekly
        try:
            user.is_subscribed_daily = False
            user.is_subscribed_weekly = True
        except Exception:
            pass
        db.session.commit()
        current_app.logger.info(f"User {user.id} subscription canceled")
        
    except Exception as e:
        current_app.logger.exception("Error handling subscription cancellation")

def handle_payment_failed(invoice):
    """Handle failed payments"""
    try:
        user = User.query.filter_by(stripe_customer_id=invoice.customer).first()
        if not user:
            current_app.logger.error(f"User not found for failed payment: {invoice.customer}")
            return
        
        user.subscription_status = "past_due"
        db.session.commit()
        current_app.logger.info(f"User {user.id} payment failed")
        
    except Exception as e:
        current_app.logger.exception("Error handling payment failure")

@bp.route("/success")
def success():
    """Success page after checkout"""
    if not stripe:
        return redirect(url_for("dashboard"))
        
    session_id = request.args.get("session_id")
    if session_id:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            # Always attempt to update the user record – some trials have payment_status="unpaid"
            try:
                # Retrieve full subscription details
                subscription = stripe.Subscription.retrieve(session.subscription)

                # Prefer updating the authenticated user if present; fallback to user_id from metadata
                target_user = None
                if current_user.is_authenticated:
                    target_user = current_user
                else:
                    try:
                        meta_user_id = int(session.metadata.get("user_id")) if session.metadata and session.metadata.get("user_id") else None
                    except Exception:
                        meta_user_id = None
                    if meta_user_id:
                        target_user = User.query.get(meta_user_id)

                if target_user:
                    target_user.subscription_status = subscription.status
                    # Store plan type from metadata if available
                    if session.metadata and session.metadata.get("plan_type"):
                        target_user.plan_type = session.metadata.get("plan_type")
                    target_user.stripe_customer_id = session.customer
                    target_user.stripe_subscription_id = session.subscription
                    # Handle trial information
                    if subscription.get("trial_end"):
                        from datetime import datetime
                        target_user.trial_end = datetime.utcfromtimestamp(subscription["trial_end"])
                        target_user.had_trial = True
                    elif subscription.status == "active":
                        target_user.trial_end = None
                    db.session.commit()
                else:
                    current_app.logger.error("Could not resolve target user for checkout success update")
            except Exception as update_err:
                current_app.logger.exception("Post-checkout user update failed")
            return render_template("billing/success.html")
        except Exception as e:
            current_app.logger.error(f"Error retrieving session: {e}")
    
    return redirect(url_for("dashboard"))

@bp.route("/sync", methods=["POST"])
@login_required
def sync_subscription():
    """Refresh the current user's subscription status from Stripe"""
    if not stripe:
        return jsonify({"error": "Stripe not available"}), 500
    if not current_user.stripe_subscription_id:
        return jsonify({"status": current_user.subscription_status, "plan": current_user.plan_type})
    try:
        subscription = stripe.Subscription.retrieve(current_user.stripe_subscription_id)
        current_user.subscription_status = subscription.status
        if subscription.get("trial_end"):
            from datetime import datetime
            current_user.trial_end = datetime.utcfromtimestamp(subscription["trial_end"])
        elif subscription.status == "active":
            current_user.trial_end = None
        db.session.commit()
        return jsonify({
            "status": current_user.subscription_status,
            "plan": current_user.plan_type,
            "trial_end": current_user.trial_end.isoformat() if current_user.trial_end else None
        })
    except Exception as e:
        current_app.logger.exception("Subscription sync failed")
        return jsonify({"error": str(e)}), 400

@bp.route("/create-portal-session", methods=["POST"])
@login_required
def create_portal_session():
    """Create a Stripe Customer Portal session"""
    if not stripe:
        return jsonify({"error": "Stripe not available"}), 500
    if not current_user.stripe_customer_id:
        return jsonify({"error": "No Stripe customer"}), 400

    try:
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=request.host_url.rstrip('/') + url_for("settings")
        )
        return jsonify({"url": session.url})
    except stripe.error.InvalidRequestError as e:
        if "billing_portal" in str(e).lower() or "configuration" in str(e).lower():
            current_app.logger.error(f"Stripe Customer Portal not configured: {e}")
            return jsonify({"error": "Billing portal not configured. Please contact support."}), 400
        else:
            current_app.logger.exception("Stripe portal error")
            return jsonify({"error": str(e)}), 400
    except Exception as e:
        current_app.logger.exception("Stripe portal error")
        return jsonify({"error": str(e)}), 400

@bp.route("/pricing")
def pricing():
    """Pricing page"""
    return render_template("pricing.html")
