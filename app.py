import os
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, extract
from sqlalchemy import event, func
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from collections import defaultdict
from flask_mail import Mail, Message
from apscheduler.schedulers.background import BackgroundScheduler
from fuzzywuzzy import fuzz


import atexit

from models import (
    db, ShiftReport, GeneratorLog, FuelLog, ReeferInventory, ReeferFault, MaintenanceTask, User, SystemSettings, TaskLog
)

# 1. INITIALIZE APP & CONFIG
app = Flask(__name__)
app.config['SECRET_KEY'] = 'enterprise_super_secret_key_2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///powerhouse_enterprise.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 🌟 CORRECTION 1: FIXED MAIL CREDENTIALS 🌟
# Removed os.environ.get() because you are providing the actual strings.
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'dualu20@gmail.com'
app.config['MAIL_PASSWORD'] = 'gqer jklo dzql pyfe' # Removed the extra line break you had here

# 2. INITIALIZE EXTENSIONS
db.init_app(app)
mail = Mail(app)
migrate = Migrate(app, db)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Please log in to access the Powerhouse Terminal."

# 3. SCHEDULER SETUP
# 🌟 CORRECTION 2: BUILT THE COMPREHENSIVE MONTHLY REPORT LOGIC 🌟
def run_monthly_report():
    with app.app_context():
        print("Executing Monthly Comprehensive Report Task...")
        try:
            from sqlalchemy import extract
            now = datetime.utcnow()
            target_month = now.month
            target_year = now.year

            # A. Calculate Total Reefers Received & Delivered
            reefers_this_month = ReeferInventory.query.join(ShiftReport).filter(
                extract('month', ShiftReport.timestamp) == target_month,
                extract('year', ShiftReport.timestamp) == target_year
            ).all()
            
            total_received = sum([int(r.received or 0) for r in reefers_this_month])
            total_delivered = sum([int(r.delivered or 0) for r in reefers_this_month])

            # B. Get all Faulty Reefers
            faults_this_month = ReeferFault.query.join(ShiftReport).filter(
                extract('month', ShiftReport.timestamp) == target_month,
                extract('year', ShiftReport.timestamp) == target_year
            ).all()

            # C. Calculate Fuel Burned per Generator
            fuel_this_month = FuelLog.query.join(ShiftReport).filter(
                extract('month', ShiftReport.timestamp) == target_month,
                extract('year', ShiftReport.timestamp) == target_year
            ).all()
            
            fuel_totals = {}
            for f in fuel_this_month:
                if f.gallons_consumed:
                    fuel_totals[f.genset_id] = fuel_totals.get(f.genset_id, 0) + float(f.gallons_consumed)

            # D. Build & Send the Email
            msg = Message(f"Monthly Comprehensive Report - {target_month}/{target_year}", 
                          sender=app.config['MAIL_USERNAME'],
                          recipients=["dualu20@gmail.com"]) # Sending to the daily receiver
            
            # Formatting the email body natively so you don't need a separate HTML template file
            html = f"<h2>Powerhouse Comprehensive Report ({target_month}/{target_year})</h2>"
            html += f"<h3>Reefer Statistics</h3><p><b>Total Received:</b> {total_received}<br><b>Total Delivered:</b> {total_delivered}</p>"
            
            html += "<h3>Generator Fuel Burn</h3><ul>"
            for gen, gallons in fuel_totals.items():
                html += f"<li><b>{gen}:</b> {gallons} Gallons</li>"
            html += "</ul>"
            
            html += "<h3>Faulty Reefer Log</h3>"
            if faults_this_month:
                html += "<table border='1' cellpadding='5'><tr><th>Unit ID</th><th>Alarm</th><th>Supply</th><th>Return</th></tr>"
                for fault in faults_this_month:
                    html += f"<tr><td>{fault.reefer_id}</td><td>{fault.alarm_code}</td><td>{fault.supply_temp}</td><td>{fault.return_temp}</td></tr>"
                html += "</table>"
            else:
                html += "<p>No faults recorded this month.</p>"

            msg.html = html
            mail.send(msg)
            print("✅ Monthly report generated and sent successfully!")
            
        except Exception as e:
            print(f"🚨 Failed to send monthly report: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(func=run_monthly_report, trigger='cron', day=29, hour=8, minute=0)

if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    scheduler.start()

atexit.register(lambda: scheduler.shutdown())

# 3. MODELS & USER LOADER
from models import User, SystemSettings, ShiftReport, ReeferInventory, ReeferFault, GeneratorLog, FuelLog, TaskLog, MaintenanceTask

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 4. AUTO-BUILD DATABASE
with app.app_context():
    db.create_all()
    # Ensure Admin and Settings exist
    if not User.query.filter_by(username='admin').first():
        master_admin = User(username='admin', password_hash=generate_password_hash('admin123'), role='super_admin')
        db.session.add(master_admin)
        db.session.commit()
    if not SystemSettings.query.first():
        default_settings = SystemSettings(company_name="Powerhouse Enterprise", logo_path="default_logo.png")
        db.session.add(default_settings)
        db.session.commit()

# 5. ROUTES
@app.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # 🔐 Redirect authenticated users who still haven't changed their password
        if current_user.must_change_password:
            return redirect(url_for('force_change_password_view'))
            
        if current_user.role == 'super_admin': return redirect(url_for('admin_dashboard'))
        if current_user.role == 'supervisor': return redirect(url_for('supervisor_dashboard'))
        return redirect(url_for('operator_portal'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            
            # 🔐 INTERCEPT BLOCK: Catch passwords flagged for updates immediately
            if user.must_change_password:
                flash('⚠️ Security Requirement: Please update your temporary password before continuing.')
                return redirect(url_for('force_change_password_view'))
            
            # If the user is clear, resume normal dashboard routing rules
            if user.role == 'super_admin': return redirect(url_for('admin_dashboard'))
            if user.role == 'supervisor': return redirect(url_for('supervisor_dashboard'))
            return redirect(url_for('operator_portal'))
        else:
            flash('Invalid username or password.')
            
    return render_template('login.html')

@app.route('/force_change_password', methods=['GET', 'POST'])
@login_required
def force_change_password_view():
    if request.method == 'POST':
        new_password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            flash("❌ Passwords do not match. Please verify your inputs.")
            return render_template('force_change.html')
            
        if new_password == "Reset123!":
            flash("❌ Security rule: You cannot reuse the temporary system password.")
            return render_template('force_change.html')

        try:
            current_user.password_hash = generate_password_hash(new_password)
            current_user.must_change_password = False 
            db.session.commit()
            
            flash("✅ Security profile updated successfully! Welcome to the system.")
            
            # 🎯 SMART DYNAMIC ROUTING: Sends each role where they belong!
            if current_user.role == 'super_admin':
                return redirect(url_for('admin_dashboard'))
            elif current_user.role == 'supervisor':
                return redirect(url_for('supervisor_dashboard'))
            else:
                return redirect(url_for('operator_portal'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"🚨 Error updating security record: {str(e)}")
            
    return render_template('force_change.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# --- OPERATOR SECTION ---

# STEP 1: Paste this new Gateway route right here
@app.route('/operator')
@login_required
def operator_portal():
    # Only allow 'operator', 'supervisor', or 'super_admin' roles
    if current_user.role not in ['operator', 'supervisor', 'super_admin']:
        flash("Unauthorized access. Please contact an administrator.", "danger")
        return redirect(url_for('login'))
        
    return render_template('shift_selection.html')


# STEP 2: Your exact loop starts here, we just modified the route string and added (shift_type)
@app.route('/operator/form/<shift_type>')
@login_required
def operator_dashboard(shift_type):
    if shift_type not in ['Day', 'Night']:
        return redirect(url_for('operator_portal'))

    system_settings = SystemSettings.query.first()
    
    next_service_data = {}
    remaining_hours_data = {}
    alert_status_data = {}  # Stores: 'normal', 'alert', or 'critical'
    
    for gen_id in ['ag2', 'ag4', 'ag5', 'ag7', 'ag8', 'ag9']:
        clean_num = gen_id.replace('ag', '')
        
        possible_ids = [
            gen_id.lower(),          # 'ag2'
            gen_id.upper(),          # 'AG2'
            f"ag-{clean_num}",       # 'ag-2'
            f"AG-{clean_num}"        # 'AG-2'
        ]
        
        # Fetch all historical logs for this unit, newest to oldest
        logs = GeneratorLog.query.filter(GeneratorLog.genset_id.in_(possible_ids)).order_by(GeneratorLog.id.desc()).all()
        
        next_srv_val = None
        # 1. FIXED: Native lookup to find the last valid Next Service Target
        for log in logs:
            if log.next_service is not None and str(log.next_service).strip() != "":
                try:
                    next_srv_val = float(log.next_service)
                    break
                except ValueError:
                    continue
                
        # 2. Scan backward to find the most recent recorded Run Hours 
        latest_run_hours = None
        for log in logs:
            if log.run_hours is not None and str(log.run_hours).strip() != "":
                try:
                    latest_run_hours = float(log.run_hours)
                    break
                except ValueError:
                    continue
        
        # 3. Apply countdown logic and evaluate thresholds
        val_display = ""
        rem_display = "N/A"
        status = "normal"
        
        if next_srv_val is not None:
            val_display = int(next_srv_val) if next_srv_val.is_integer() else next_srv_val
            
            if latest_run_hours is not None:
                remaining = next_srv_val - latest_run_hours
                rem_display = round(remaining, 1)
                
                if remaining <= 49:
                    status = "critical"
                elif remaining <= 99:
                    status = "alert"
                    
        # Bulletproof dictionary mapping: store values under every possible key style
        for key in [clean_num, int(clean_num), gen_id, gen_id.upper(), f"AG-{clean_num}"]:
            next_service_data[key] = val_display
            remaining_hours_data[key] = rem_display
            alert_status_data[key] = status

  
    return render_template('operator.html', 
                           settings=system_settings, 
                           current_user=current_user,
                           next_service_data=next_service_data,
                           remaining_hours_data=remaining_hours_data,
                           alert_status_data=alert_status_data,
                           shift_type=shift_type) # 🌟 ENSURE THIS EXACT LINE IS HERE


@app.route('/submit_shift', methods=['POST'])
@login_required
def submit_shift():
    # 📅 1. GRAB THE OPERATOR'S PICKED DATE FROM THE FORM
    date_str = request.form.get('report_date')  
    chosen_shift = request.form.get('shift_type', 'Day')
    
    # Default fallback to current UTC time if the picker is somehow empty
    report_timestamp = datetime.utcnow() 
    if date_str:
        try:
            # Convert the 'YYYY-MM-DD' text string from HTML into a real Python datetime object
            report_timestamp = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            pass  # Fall back to default if format is invalid

    # 💾 2. CREATE REPORT WITH THE CHOSEN BACKDATE
    new_report = ShiftReport(
        submitted_by=current_user.username,
        timestamp=report_timestamp,
        shift_type=chosen_shift
    )
    db.session.add(new_report)
    db.session.flush()
    
    # Reefer Data
    reefer_data = ReeferInventory(
        report_id=new_report.id,
        start_count=request.form.get('reefer_start'),
        received=request.form.get('reefer_received'),
        delivered=request.form.get('reefer_delivered'),
        end_count=request.form.get('reefer_end')
    )
    db.session.add(reefer_data)
    
    # Faulty Reefers
    i = 1
    while True:
        unit_id = request.form.get(f'fault_id_{i}')
        if not unit_id: break
        if unit_id.strip() != "":
            db.session.add(ReeferFault(
                report_id=new_report.id, 
                reefer_id=unit_id.strip(), 
                setpoint=request.form.get(f'fault_setpoint_{i}'), 
                supply_temp=request.form.get(f'fault_supply_{i}'), 
                return_temp=request.form.get(f'fault_return_{i}'), 
                alarm_code=request.form.get(f'fault_alarm_{i}')
            ))
        i += 1

    # ⛽ NEW: COMMIT EACH DYNAMIC FUEL ROW ENTRY TO THE DATABASE
    total_fuel_rows = int(request.form.get('fuel_row_count', 1))
    for fuel_idx in range(1, total_fuel_rows + 1):
        t_val = request.form.get(f'fuel_time_{fuel_idx}')
        if t_val and t_val.strip() != "":
            for clean_num in ['2', '4', '5', '7', '8', '9']:
                gal_str = request.form.get(f'fuel_ag{clean_num}_{fuel_idx}')
                if gal_str and gal_str.strip() != "":
                    try:
                        fuel_record = FuelLog(
                            report_id=new_report.id,
                            genset_id=f"AG-{clean_num}",
                            time_recorded=t_val,
                            gallons_consumed=float(gal_str)
                        )
                        db.session.add(fuel_record)
                    except ValueError:
                        pass

    active_maintenance_alerts = []

    # Generators
    for gen_id in ['ag2', 'ag4', 'ag5', 'ag7', 'ag8', 'ag9']:
        clean_num = gen_id.replace('ag', '')
        possible_ids = [gen_id.upper(), f"AG-{clean_num}"]
        
        h = request.form.get(f'{gen_id}_hours')
        next_srv = request.form.get(f'{gen_id}_next_service')

        log_entry = GeneratorLog(
            report_id=new_report.id, 
            genset_id=gen_id.upper(), 
            volts=request.form.get(f'{gen_id}_volts') or None, 
            amps=request.form.get(f'{gen_id}_amps') or None, 
            load_pct=request.form.get(f'{gen_id}_load') or None, 
            kw=request.form.get(f'{gen_id}_kw') or None, 
            battery_v=request.form.get(f'{gen_id}_batt') or None, 
            temp_c=request.form.get(f'{gen_id}_temp') or None, 
            run_hours=h or None
        )
        
        # Fetch previous log for inheritance and SFC delta calculation
        prev_log = GeneratorLog.query.filter(GeneratorLog.genset_id.in_(possible_ids)).order_by(GeneratorLog.id.desc()).first()

        # Inheritance logic: If left blank, pull old target hours forward
        final_next_service = None
        if next_srv and next_srv.strip() != "":
            final_next_service = float(next_srv)
        else:
            if prev_log and prev_log.next_service is not None and str(prev_log.next_service).strip() != "":
                try:
                    final_next_service = float(prev_log.next_service)
                except ValueError:
                    final_next_service = None

        if final_next_service is not None:
            if final_next_service.is_integer():
                log_entry.next_service = str(int(final_next_service))
            else:
                log_entry.next_service = str(final_next_service)
                
        db.session.add(log_entry)
        # 🛠️ PROCESS MAINTENANCE TASKS (Updated to match models.py)
    t_idx = 1
    while True:
        task_type = request.form.get(f'task_type_{t_idx}')
        # Break loop if we stop finding task types
        if not task_type:
            break
            
        if task_type != 'none':
            new_task = MaintenanceTask(
                task_type=task_type,
                asset_id=request.form.get(f'task_asset_{t_idx}'),
                notes=request.form.get(f'task_notes_{t_idx}'),
                progress=int(request.form.get(f'task_progress_{t_idx}', 0)),
                status='Active' # Default status as per your model
                # Note: 'before_photo' and 'after_photo' logic would go here
                # if you are capturing file paths from request.files or request.form
            )
            db.session.add(new_task)
        t_idx += 1
        # Real-time alert threshold checking during submission
        current_h = float(h) if (h and h.strip() != "") else None
        if current_h is None:
            if prev_log and prev_log.run_hours is not None and str(prev_log.run_hours).strip() != "":
                try:
                    current_h = float(prev_log.run_hours)
                except ValueError:
                    current_h = None

        if final_next_service is not None and current_h is not None:
            remaining = final_next_service - current_h
            if remaining <= 49:
                active_maintenance_alerts.append(f"⚠️ CRITICAL: Unit AG-{clean_num} has only {round(remaining, 1)} runtime hours remaining before service!")
            elif remaining <= 99:
                active_maintenance_alerts.append(f"🚨 ALERT: Unit AG-{clean_num} has only {round(remaining, 1)} runtime hours remaining before service.")

        # --- ADVANCED SPECIFIC FUEL CONSUMPTION (SFC) MATHEMATICAL ENGINES ---
        kw_input = request.form.get(f'{gen_id}_kw')
        if kw_input and kw_input.strip() != "":
            try:
                current_kwhr_val = float(kw_input)
                if prev_log and prev_log.kw and str(prev_log.kw).strip() != "":
                    prev_kwhr_val = float(prev_log.kw)
                    delta_kwhr = current_kwhr_val - prev_kwhr_val
                    
                    if delta_kwhr < 0:
                        active_maintenance_alerts.append(
                            f"❌ DATA ANOMALY: Unit AG-{clean_num} current kWhr counter ({current_kwhr_val}) is lower than the previous recorded shift counter ({prev_kwhr_val}). Check for odometer rollover or entry typo!"
                        )
                    elif delta_kwhr > 0:
                        total_gen_gallons = 0.0
                        
                        # Updated to step safely through bounded fuel_row_count tracker
                        for fuel_idx in range(1, total_fuel_rows + 1):
                            t_val = request.form.get(f'fuel_time_{fuel_idx}')
                            if not t_val: 
                                continue
                            gal_str = request.form.get(f'fuel_ag{clean_num}_{fuel_idx}')
                            if gal_str and gal_str.strip() != "":
                                try:
                                    total_gen_gallons += float(gal_str)
                                except ValueError:
                                    pass
                            
                        total_gen_liters = total_gen_gallons * 3.78541
                        sfc_current = total_gen_liters / delta_kwhr
                        
                        if sfc_current > 0.45:
                            active_maintenance_alerts.append(
                                f"⚠️ HIGH SFC ANOMALY: Unit AG-{clean_num} has an elevated Specific Fuel Consumption (SFC) of {round(sfc_current, 4)} L/kWhr! Inspect for active structural fuel leaks, mechanical drag, or incorrect manual entries."
                            )
                        elif sfc_current < 0.15 and total_gen_gallons > 0:
                            active_maintenance_alerts.append(
                                f"⚠️ LOW SFC ANOMALY: Unit AG-{clean_num} has an abnormally low Specific Fuel Consumption (SFC) of {round(sfc_current, 4)} L/kWhr! Verify if some refueling volumes were left unrecorded."
                            )
            except ValueError:
                pass

    # 🔄 3. COMMIT SHIFT DATA ENTRIES TO SQLITE
    try:
        db.session.commit()
        
        # --- EMAIL NOTIFICATION LOGIC ---
        # 🌟 CORRECTION 3: CONFIGURED RECIPIENTS, CCs, AND SENDERS 🌟
        # 1. Main Report Email
        try:
            msg = Message(f"Shift Report Submitted: {chosen_shift} Shift", 
                          sender=app.config['MAIL_USERNAME'],
                          recipients=["dualu20@gmail.com"], # The main receiver
                          cc=["hydeleslie8@gmail.com", "jw842509@gmail.com"]) # Change these to the real CC emails
            
            # Rendering HTML layout if it exists
            msg.html = render_template('email/shift_report.html', report=new_report)
            mail.send(msg)
        except Exception as e:
            print(f"Main email failed: {e}")

        # 2. Logic for Reefer Faults
        faults = ReeferFault.query.filter_by(report_id=new_report.id).all()
        if faults:
            try:
                fault_msg = Message("🚨 ALERT: Reefer Faults Detected", 
                                    sender=app.config['MAIL_USERNAME'],
                                    recipients=["jw842509@gmail.com"]) # Change to maintenance email
                fault_msg.html = render_template('email/fault_report.html', faults=faults)
                mail.send(fault_msg)
            except Exception as e:
                print(f"Fault email failed: {e}")
        # --------------------------------

        # 🛡️ THE SECURITY CLEANUP FIX (Keep your existing alert printing)
        if active_maintenance_alerts:
            for alert in active_maintenance_alerts:
                print(f"[ENGINEERING AUDIT LOG] {alert}")

        flash("Shift report submitted successfully!")
        
    except Exception as e:
        db.session.rollback()
        flash(f"🚨 Critical database write fault: {str(e)}")

    return redirect(url_for('operator_dashboard', shift_type=chosen_shift))



# --- SUPERVISOR SECTION ---
@app.route('/supervisor', methods=['GET', 'POST'])
@login_required
def supervisor_dashboard():
    # 🌟 EXTRACT: Query parameters
    start_date_str = request.args.get('start_date', '')
    active_tab = request.args.get('active_tab', '') 
    
    # 🏎️ Auto-routing logic
    if start_date_str and active_tab:
        try:
            from datetime import datetime
            search_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            shift_name = 'Day' if active_tab == 'day' else 'Night'
            
            target_report = ShiftReport.query.filter(
                db.func.date(ShiftReport.timestamp) == search_date,
                ShiftReport.shift_type == shift_name
            ).first()
            
            if target_report:
                return redirect(url_for('review_shift', report_id=target_report.id))
            else:
                flash(f"No operational {shift_name} shift report was found for {start_date_str}.", "warning")
        except Exception as e:
            print(f"Error executing auto-routing sequence: {e}")

    # 📊 Metrics Engine
    from datetime import datetime as dt, timedelta
    weekly_window_date = dt.now() - timedelta(days=7)
    all_fuel_logs = FuelLog.query.all()
    
    total_gallons = 0.0
    for f_log in all_fuel_logs:
        log_date = None
        if hasattr(f_log, 'report') and f_log.report and hasattr(f_log.report, 'timestamp'):
            log_date = f_log.report.timestamp
        elif hasattr(f_log, 'timestamp') and f_log.timestamp:
            log_date = f_log.timestamp

        if log_date is None or log_date >= weekly_window_date:
            if f_log.gallons_consumed and f_log.gallons_consumed.strip() != "":
                try: total_gallons += float(f_log.gallons_consumed)
                except ValueError: continue

    total_kwh = 0.0
    distinct_gensets = db.session.query(GeneratorLog.genset_id).distinct().all()
    genset_ids = [g[0] for g in distinct_gensets if g[0]]
    HARDCODED_BASELINES = {'ag 2': 1290.0, 'ag 4': 4472352.0, 'ag 5': 3448095.0, 'ag 7': 13837.0, 'ag 8': 1096792.0, 'ag 9': 1081938.0}

    active_maintenance_alerts = []
    for g_id in genset_ids:
        all_logs_for_unit = GeneratorLog.query.filter_by(genset_id=g_id).order_by(GeneratorLog.id.asc()).all()
        clean_num = str(g_id).lower().replace('ag', '').strip()
        for i in range(0, len(all_logs_for_unit)):
            current_log = all_logs_for_unit[i]
            if i == 0: continue
            previous_log = all_logs_for_unit[i - 1]
            parent_report = db.session.get(ShiftReport, current_log.report_id) if hasattr(db.session, 'get') else ShiftReport.query.get(current_log.report_id)
            
            log_date = parent_report.timestamp if parent_report else None
            shift_name_str = parent_report.shift_type.lower() if (parent_report and parent_report.shift_type) else 'day'
            
            if current_log.kw:
                try:
                    curr_val = float(current_log.kw.strip())
                    prev_val = HARDCODED_BASELINES[g_id] if (i - 1 == 0 and g_id in HARDCODED_BASELINES) else float(previous_log.kw.strip()) if previous_log.kw else curr_val
                    shift_delta = curr_val - prev_val
                    if shift_delta >= 0: total_kwh += shift_delta
                    if shift_delta < 0:
                        active_maintenance_alerts.append({'genset_id': g_id, 'report_id': current_log.report_id, 'shift_type': shift_name_str, 'date': log_date.strftime('%b %d, %Y') if log_date else 'Unknown Date', 'message': f"❌ DATA ANOMALY: Unit AG-{clean_num} current kWhr counter..."})
                    
                    total_gen_gallons = 0.0
                    if parent_report and hasattr(parent_report, 'fuel_logs'):
                        for fl in parent_report.fuel_logs:
                            if fl.genset_id == g_id and fl.gallons_consumed:
                                try: total_gen_gallons += float(fl.gallons_consumed)
                                except ValueError: pass
                    sfc_current = (total_gen_gallons / shift_delta) if shift_delta > 0 else 0.0
                    if sfc_current > 0.45 or (sfc_current < 0.15 and total_gen_gallons > 0):
                        active_maintenance_alerts.append({'genset_id': g_id, 'report_id': current_log.report_id, 'shift_type': shift_name_str, 'message': f"⚠️ SFC ANOMALY: Unit AG-{clean_num} has an SFC of {round(sfc_current, 4)} L/kWhr."})
                except ValueError: continue

            if current_log.run_hours:
                try:
                    hours_val = float(current_log.run_hours)
                    remaining = 250 - (hours_val % 250)
                    if remaining <= 99:
                        active_maintenance_alerts.append({'genset_id': g_id, 'report_id': current_log.report_id, 'shift_type': shift_name_str, 'message': f"🚨 ALERT: Unit AG-{clean_num} has {round(remaining, 1)} hours remaining."})
                except ValueError: pass

    sfc_metric = round(total_gallons / total_kwh, 3) if total_kwh > 0 else 0.0
    active_faults_count = len(active_maintenance_alerts)
    system_health = "Critical" if active_faults_count > 3 else "Warning" if active_faults_count > 0 else "Optimal"

    # 🌟 SURGICAL INJECTION: Calculate fuel per report
    reports = ShiftReport.query.order_by(ShiftReport.timestamp.desc()).limit(10).all()
    for report in reports:
        total = db.session.query(db.func.sum(FuelLog.gallons_consumed)).filter(FuelLog.report_id == report.id).scalar() or 0.0
        report.calculated_fuel_total = round(total, 2)

    maintenance_tasks = MaintenanceTask.query.all()
    
    return render_template('supervisor.html',
                           current_user=current_user,
                           total_kwh=round(total_kwh, 1),
                           total_gallons=round(total_gallons, 1),
                           sfc_metric=sfc_metric,
                           active_faults_count=active_faults_count,
                           system_health=system_health,
                           reports=reports,
                           maintenance_tasks=maintenance_tasks,
                           tasks=maintenance_tasks,
                           active_maintenance_alerts=active_maintenance_alerts,
                           start_date=start_date_str,
                           end_date='',
                           day_report=None,
                           night_report=None,
                           search_triggered=False,
                           active_tab=active_tab)

# --- ADMIN SECTION ---
@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    # 🛡️ Access Control Security Check
    if current_user.role != 'super_admin':
        return "Access Denied. Super Admins Only.", 403
        
    # Ensure System Settings baseline is populated
    system_settings = SystemSettings.query.first()
    if not system_settings:
        system_settings = SystemSettings(company_name="Powerhouse Enterprise", logo_path="default_logo.png")
        db.session.add(system_settings)
        db.session.commit()

    # Query the list of system users to populate your active directory management table
    all_users = User.query.all()
    
    return render_template('admin.html', 
                           settings=system_settings, 
                           users=all_users, 
                           current_user=current_user)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    from flask import session; session.pop('_flashes', None)

    # 🛡️ Aligned Security Check
    if current_user.role != 'super_admin':
        flash("Unauthorized action. Super Admins Only.")
        return redirect('/')

    # 🔐 Self-Deletion Guardrail
    if current_user.id == user_id:
        flash("❌ Operational error: You cannot delete your own active account.")
        return redirect('/admin') # 👈 Hardcoded direct string URL route path

    # Find the user matching the structural class name 'User'
    user_to_delete = User.query.get(user_id)
    if not user_to_delete:
        flash("❌ User not found.")
        return redirect('/admin')

    try:
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f"✅ Account for {user_to_delete.username} has been successfully deleted!")
    except Exception as e:
        db.session.rollback()
        flash(f"🚨 Database error during deletion: {str(e)}")

    return redirect('/admin') # 👈 Hardcoded direct string URL route path


@app.route('/admin/reset_password/<int:user_id>')
@login_required
def reset_password(user_id):
    from flask import session; session.pop('_flashes', None)

    # 🛡️ Aligned Security Check
    if current_user.role != 'super_admin':
        flash("Unauthorized action. Super Admins Only.")
        return redirect('/')

    user_to_reset = User.query.get(user_id)
    if not user_to_reset:
        flash("❌ User not found.")
        return redirect('/admin')

    try:
        user_to_reset.password_hash = generate_password_hash("Reset123!")
        user_to_reset.must_change_password = True
        db.session.commit()
        flash(f"✅ Password for {user_to_reset.username} has been reset to: Reset123!")
    except Exception as e:
        db.session.rollback()
        flash(f"🚨 Database error during reset: {str(e)}")

    return redirect('/admin')

@app.route('/supervisor/update_logs', methods=['POST'])
def update_logs():
    redirect_date = request.form.get('redirect_date', '')
    day_report_id = request.form.get('day_report_id')
    night_report_id = request.form.get('night_report_id')
    
    for key, value in request.form.items():
        if not value.strip(): 
            continue
            
        # 1. Direct Run Hours Correction Override
        if '_hours_' in key:
            parts = key.split('_hours_')
            shift_type = parts[0]   
            g_id = parts[1]         
            report_id = day_report_id if shift_type == 'day' else night_report_id
            if report_id:
                log = GeneratorLog.query.filter_by(report_id=report_id, genset_id=g_id).first()
                if log:
                    try:
                        log.run_hours = float(value)
                        db.session.commit()
                    except ValueError: pass

        # 2. Direct kWh Counter Correction Override
        elif '_kw_' in key:
            parts = key.split('_kw_')
            shift_type = parts[0]   
            g_id = parts[1]         
            report_id = day_report_id if shift_type == 'day' else night_report_id
            if report_id:
                log = GeneratorLog.query.filter_by(report_id=report_id, genset_id=g_id).first()
                if log:
                    try:
                        log.kw = str(int(float(value)))  
                        db.session.commit()
                    except ValueError: pass

        # ⛽ 3. INDUSTRY STANDARD: Direct Fuel Consumption Total Value Overwrite
        elif '_fuel_total_' in key:
            parts = key.split('_fuel_total_')
            shift_type = parts[0]
            g_id = parts[1]
            report_id = day_report_id if shift_type == 'day' else night_report_id
            
            if report_id:
                f_log = FuelLog.query.filter_by(report_id=report_id, genset_id=g_id).first()
                if f_log:
                    try:
                        # Replaces the historical entry directly with the verified actual value
                        f_log.gallons_consumed = float(value)
                        db.session.commit()
                    except ValueError: pass

    return redirect(url_for('supervisor_dashboard', start_date=redirect_date))

@app.route('/update_branding', methods=['POST'])
@login_required
def update_branding():
    if current_user.role != 'super_admin':
        return "Access Denied. Super Admins Only.", 403

    system_settings = SystemSettings.query.first()
    if not system_settings:
        system_settings = SystemSettings(company_name="Powerhouse Enterprise", logo_path="default_logo.png")
        db.session.add(system_settings)

    new_name = request.form.get('company_name')
    if new_name:
        system_settings.company_name = new_name

    if 'logo_file' in request.files:
        file = request.files['logo_file']
        if file and file.filename != '':
            ext = os.path.splitext(secure_filename(file.filename))[1]
            saved_filename = f"custom_logo{ext}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], saved_filename))
            system_settings.logo_path = saved_filename

    db.session.commit()
    flash("System branding updated successfully!")
    return redirect(url_for('admin_dashboard'))


@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    from flask import session; session.pop('_flashes', None)

    # 🛡️ ONLY block users who are NOT admins. If they ARE an admin, let them pass!
    if current_user.role != 'super_admin' and current_user.role != 'supervisor':
        flash("Unauthorized action.")
        return redirect(url_for('login'))

    # Grab the data from your HTML form fields
    username = request.form.get('new_username')
    password = request.form.get('new_password')
    role = request.form.get('new_role')

    # Double check we actually got data so we don't save blank rows
    if not username or not password:
        flash("❌ Username and password are required.")
        return redirect(request.referrer or url_for('admin_dashboard'))

    # Check if the username is already taken
    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        flash("❌ Username already exists!")
        return redirect(request.referrer or url_for('admin_dashboard'))

    try:
        # Create and save the user safely
        new_user = User(
            username=username,
            password_hash=generate_password_hash(password), 
            role=role,
            must_change_password=True # 👈 The single clean update added here!
        )
        db.session.add(new_user)
        db.session.commit()

        flash(f"✅ Account created successfully for {username} as {role}!")
    except Exception as e:
        db.session.rollback()
        flash(f"🚨 Database error: {str(e)}")

    # Go right back to where you came from
    return redirect(request.referrer or url_for('admin_dashboard'))


# --- SHIFT REVIEW & VERIFICATION HELPER METRICS ---
def get_delta_kwhr(log):
    if not log.kw: 
        return 0.0
    try:
        current_val = float(log.kw)
    except ValueError:
        return 0.0
    clean_num = log.genset_id.upper().replace('AG', '').replace('-', '').strip()
    possible_ids = [f"AG{clean_num}", f"ag{clean_num}", f"AG-{clean_num}", f"ag-{clean_num}"]
    prev_log = GeneratorLog.query.filter(
        GeneratorLog.id < log.id,
        GeneratorLog.genset_id.in_(possible_ids)
    ).order_by(GeneratorLog.id.desc()).first()
    if prev_log and prev_log.kw:
        try:
            return max(0.0, current_val - float(prev_log.kw))
        except ValueError:
            return 0.0
    return 0.0


def get_fuel_liters(log):
    # BULLETPROOF CLEANING: Strip 'AG', 'ag', and '-' to get just the raw number
    clean_num = log.genset_id.upper().replace('AG', '').replace('-', '').strip()
    possible_ids = [f"AG{clean_num}", f"ag{clean_num}", f"AG-{clean_num}", f"ag-{clean_num}"]
    
    fuel_entries = FuelLog.query.filter(
        FuelLog.report_id == log.report_id, 
        FuelLog.genset_id.in_(possible_ids)
    ).all()
    
    total_gallons = 0.0
    for entry in fuel_entries:
        if entry.gallons_consumed:
            try:
                total_gallons += float(entry.gallons_consumed)
            except ValueError:
                pass
    return total_gallons * 3.78541

# --- SHIFT REVIEW & VERIFICATION ---
@app.route('/review_shift/<int:report_id>', methods=['GET', 'POST'])
@login_required
def review_shift(report_id):
    if current_user.role != 'supervisor' and current_user.role != 'super_admin':
        flash("Unauthorized access.")
        return redirect(url_for('login'))
        
    report = ShiftReport.query.get_or_404(report_id)
    generator_logs = GeneratorLog.query.filter_by(report_id=report_id).all()
    reefer_inventory = ReeferInventory.query.filter_by(report_id=report_id).first()
    reefer_faults = ReeferFault.query.filter_by(report_id=report_id).all()
    fuel_logs = FuelLog.query.filter_by(report_id=report_id).all()
    
    # 🌟 INTEGRATED SQL LOGIC (Date-based aggregation)
    report_date_prefix = report.timestamp.strftime('%Y-%m-%d') + '%'
    fuel_data = db.session.query(
        FuelLog.genset_id, 
        func.sum(func.cast(FuelLog.gallons_consumed, db.Float)).label('total_consumed')
    ).join(ShiftReport, FuelLog.report_id == ShiftReport.id)\
     .filter(ShiftReport.timestamp.like(report_date_prefix))\
     .group_by(FuelLog.genset_id)\
     .all()

    fuel_totals = {row.genset_id: (row.total_consumed or 0.0) for row in fuel_data}
    
    if request.method == 'POST':
        report.status = request.form.get('status')
        
        # Save Generator Logs (Restored)
        for gen in generator_logs:
            gen.volts = request.form.get(f'volts_{gen.id}')
            gen.amps = request.form.get(f'amps_{gen.id}')
            gen.load_pct = request.form.get(f'load_pct_{gen.id}')
            gen.kw = request.form.get(f'kw_{gen.id}')
            gen.battery_v = request.form.get(f'battery_v_{gen.id}')
            gen.temp_c = request.form.get(f'temp_c_{gen.id}')
            gen.run_hours = request.form.get(f'hours_{gen.id}')
            gen.next_service = request.form.get(f'next_service_{gen.id}')

        # Save Reefer Faults (Restored)
        for ref in reefer_faults:
            ref.temperature = request.form.get(f'reefer_temp_{ref.id}')
            ref.status = request.form.get(f'reefer_status_{ref.id}')

        # Save individual Fuel Logs (Restored)
        for fuel in fuel_logs:
            fuel.gallons_consumed = request.form.get(f'fuel_consumed_{fuel.id}')
            fuel.gallons_added = request.form.get(f'fuel_added_{fuel.id}')

        db.session.commit()
        flash(f"✅ Shift report #{report_id} updated successfully!")
        return redirect(url_for('supervisor_dashboard'))
        
    return render_template(
        'review_shift.html', 
        report=report, 
        generator_logs=generator_logs,
        fuel_logs=fuel_logs, 
        fuel_totals=fuel_totals,
        current_user=current_user,
        reefer_inventory=reefer_inventory,
        reefer_faults=reefer_faults
    )

@app.route('/analytics')
@login_required
def analytics():
    import re
    import calendar
    from datetime import datetime
    from flask import request
    
    # 🗓️ 1. Extract Selected Month from URL
    target_month_str = request.args.get('target_month')
    if not target_month_str:
        target_month_str = datetime.now().strftime('%Y-%m')
        
    try:
        target_year, target_month = map(int, target_month_str.split('-'))
    except (ValueError, AttributeError):
        target_year, target_month = datetime.now().year, datetime.now().month
        target_month_str = f"{target_year:04d}-{target_month:02d}"

    # 🧮 2. Calculate the exact number of days for this specific month
    _, num_days = calendar.monthrange(target_year, target_month)
    days = list(range(1, num_days + 1)) 
    
    all_gen_ids = ["AG-2", "AG-4", "AG-5", "AG-7", "AG-8", "AG-9"]
    
    chart_data = {
        gen: {"load": [0]*num_days, "l_hr": [0.0]*num_days, "l_kwh": [0.0]*num_days} 
        for gen in all_gen_ids
    }
    
    fuel_logs = FuelLog.query.all()
    gen_logs = GeneratorLog.query.all()
    reports_map = {r.id: r for r in ShiftReport.query.all()}
    
    def normalize_gen_id(genset_id):
        if not genset_id:
            return None
        match = re.search(r'\d+', str(genset_id))
        if match:
            return f"AG-{match.group()}"
        return str(genset_id).upper().strip()

    # 📦 Step A: Pair up logs chronologically and SUM all fuel drops per shift
    paired_logs = []
    
    for g_rec in gen_logs:
        r_id = g_rec.report_id
        if r_id not in reports_map:
            try: r_id = int(r_id)
            except (ValueError, TypeError): pass
            
        if r_id in reports_map and reports_map[r_id].timestamp:
            report = reports_map[r_id]
            gen = normalize_gen_id(g_rec.genset_id)
            
            if gen in chart_data:
                # ⛽ CRITICAL FIX: Sum ALL fuel entries for this specific shift report
                f_gals = 0.0
                for f_rec in fuel_logs:
                    f_r_id = f_rec.report_id
                    try:
                        if (f_r_id == r_id or int(f_r_id) == int(r_id)) and normalize_gen_id(f_rec.genset_id) == gen:
                            # Use += to stack every fuel drop together
                            f_gals += float(f_rec.gallons_consumed or 0)
                    except (ValueError, TypeError):
                        pass
                
                # Extract clean date details
                if isinstance(report.timestamp, str):
                    try:
                        parts = report.timestamp.split('-')
                        y, m, d = int(parts[0]), int(parts[1]), int(parts[2].split()[0])
                        ts_str = report.timestamp
                    except (IndexError, ValueError):
                        continue
                else:
                    y, m, d = report.timestamp.year, report.timestamp.month, report.timestamp.day
                    ts_str = report.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                    
                paired_logs.append({
                    'gen': gen, 'year': y, 'month': m, 'day': d, 'timestamp': ts_str,
                    'kw': g_rec.kw, 'run_hours': g_rec.run_hours, 'gallons': f_gals
                })

    # Sort everything from oldest to newest so the odometer tracks forward cleanly across ALL shifts
    paired_logs.sort(key=lambda x: x['timestamp'])

    # 📈 Step B: Aggregate Daily Totals (Combine Day & Night Shifts)
    daily_aggregates = {}
    prev_hours = {}
    
    for item in paired_logs:
        gen = item['gen']
        log_y = item['year']
        log_m = item['month']
        log_d = item['day']
        
        is_target_month = (log_y == target_year and log_m == target_month)
        
        try:
            current_hours = float(item['run_hours']) if item['run_hours'] else None
            kw_val = float(item['kw'] or 0)
        except (ValueError, TypeError):
            current_hours, kw_val = None, 0.0

        liters_burned = item['gallons'] * 3.78541
        
        if is_target_month:
            # Initialize daily bucket if not exists
            date_key = (log_y, log_m, log_d)
            if date_key not in daily_aggregates:
                daily_aggregates[date_key] = {g: {"liters": 0.0, "hours_run": 0.0, "max_kw": 0.0} for g in all_gen_ids}
            
            # Add liters to daily total (Stacks Day and Night together)
            daily_aggregates[date_key][gen]["liters"] += liters_burned
            
            # Save the highest KW load for that day to measure peak efficiency
            if kw_val > daily_aggregates[date_key][gen]["max_kw"]:
                daily_aggregates[date_key][gen]["max_kw"] = kw_val
        
        # Calculate True Run Hours for this specific shift and add to daily total
        if current_hours is not None and gen in prev_hours and prev_hours[gen] is not None:
            delta_hours = current_hours - prev_hours[gen]
            # Ensure we don't calculate negative hours if an odometer was typed wrong
            if delta_hours > 0 and is_target_month:
                daily_aggregates[date_key][gen]["hours_run"] += delta_hours

        # Slide odometer reading forward for the next shift loop
        if current_hours is not None:
            prev_hours[gen] = current_hours

    # 📊 Step C: Map Aggregated 24-Hour Totals to Graph Arrays
    for (y, m, d), gen_data in daily_aggregates.items():
        day_idx = d - 1
        if 0 <= day_idx < num_days:
            for gen in all_gen_ids:
                total_liters = gen_data[gen]["liters"]
                total_hours = gen_data[gen]["hours_run"]
                peak_kw = gen_data[gen]["max_kw"]
                
                # 1. Plot Peak Load
                chart_data[gen]["load"][day_idx] = peak_kw
                
                # 2. Plot True L/hr (Total Daily Liters / Total Daily Hours)
                if total_hours > 0 and total_liters > 0:
                    chart_data[gen]["l_hr"][day_idx] = round(total_liters / total_hours, 2)
                    
                    # 3. Plot L/kWh Efficiency based on peak load
                    if peak_kw > 0:
                        chart_data[gen]["l_kwh"][day_idx] = round((total_liters / peak_kw) * 1000, 4)

    return render_template('analytics.html', data=chart_data, days=days, target_month=target_month_str)


@app.route('/import_historical_excel', methods=['POST'])
@login_required
def import_historical_excel():
    if 'file' not in request.files:
        return redirect(request.url)
    
    file = request.files['file']
    # Use pandas to read the CSV, handling the dynamic header issue
    # We read without specifying header=0 so we can search the whole matrix
    df = pd.read_csv(file, header=None)
    data = df.values.tolist()

    try:
        # 1. HELPER: Find value in a grid using fuzzy matching on row/col headers
        def find_value_near(keyword, search_range=5):
            for r_idx, row in enumerate(data):
                for c_idx, cell in enumerate(row):
                    if isinstance(cell, str) and fuzz.partial_ratio(keyword.lower(), cell.lower()) > 85:
                        # Return the cell to the right (assuming data follows header)
                        return data[r_idx][c_idx + 1]
            return None

        # 2. EXTRACT SHIFT INFO
        # We search the grid for these unique identifying labels
        shift_date = find_value_near("date") or datetime.now().strftime("%Y-%m-%d")
        
        # 3. MAPPING LOGIC (Example for Powerhouse)
        # Instead of guessing index [3][1], we search for the label
        gen_data = {
            "AG-08": find_value_near("AG-08"),
            "Current": find_value_near("Current (AMP)"),
            "RunningHours": find_value_near("Running hours")
        }

        # 4. PERFORM DATABASE INSERTION
        # (Replace existing logic inside this route with your cleaner model creation)
        new_log = GeneratorLog(
            date=shift_date,
            ag_name="AG-08",
            running_hours=float(str(gen_data["RunningHours"]).replace(',', '')),
            current=float(str(gen_data["Current"]).replace(',', ''))
        )
        db.session.add(new_log)
        db.session.commit()
        
        flash("✅ Data imported successfully using fuzzy search.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"❌ Extraction failed: {str(e)}", "danger")

    return redirect(url_for('admin_dashboard')) # 👈 Corrected redirection


if __name__ == '__main__':
    with app.app_context(): 
        db.create_all()
    app.run(debug=True, port=5000)
