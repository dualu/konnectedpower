from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
import re

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default='operator')
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)

class SystemSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100))
    logo_path = db.Column(db.String(100))

class ShiftReport(db.Model):
    __tablename__ = 'shift_report'
    id = db.Column(db.Integer, primary_key=True)
    submitted_by = db.Column(db.String(80))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    # NEW: Added shift_type column to safely differentiate Day and Night reports
    shift_type = db.Column(db.String(10), nullable=False, default='Day')

class ReeferInventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('shift_report.id'))
    start_count = db.Column(db.Integer)
    received = db.Column(db.Integer)
    delivered = db.Column(db.Integer)
    end_count = db.Column(db.Integer)

class ReeferFault(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('shift_report.id'))
    reefer_id = db.Column(db.String(50))
    setpoint = db.Column(db.String(50))
    supply_temp = db.Column(db.String(50))
    return_temp = db.Column(db.String(50))
    alarm_code = db.Column(db.String(50))

class GeneratorLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('shift_report.id'))
    genset_id = db.Column(db.String(20))
    volts = db.Column(db.String(20))
    amps = db.Column(db.String(20))
    load_pct = db.Column(db.String(20))
    kw = db.Column(db.String(20))
    battery_v = db.Column(db.String(20))
    temp_c = db.Column(db.String(20))
    run_hours = db.Column(db.String(20))
    # FIXED FEATURE PRESERVED: Next Service threshold values column
    next_service = db.Column(db.String(20), nullable=True)

class FuelLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('shift_report.id'))
    time_recorded = db.Column(db.String(20))
    genset_id = db.Column(db.String(20))
    gallons_consumed = db.Column(db.String(20))

class TaskLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('shift_report.id'))
    task_type = db.Column(db.String(100))
    asset_id = db.Column(db.String(50))
    notes = db.Column(db.Text)
    progress_pct = db.Column(db.Integer)
    image_before = db.Column(db.String(200))
    # FIXED FEATURE PRESERVED: Form matching "After Photo" column
    image_after = db.Column(db.String(200), nullable=True)

class MaintenanceTask(db.Model):
    __tablename__ = 'maintenance_task'
    
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.String(100))
    notes = db.Column(db.Text)
    progress = db.Column(db.Integer)
    status = db.Column(db.String(50), default='Active')
    task_type = db.Column(db.String(100))
    
    # Photo drawer verification paths
    before_photo = db.Column(db.String(255), nullable=True)
    after_photo = db.Column(db.String(255), nullable=True)

class MonthlyAnalyticsCache(db.Model):
    __tablename__ = 'monthly_analytics_cache'
    
    id = db.Column(db.Integer, primary_key=True)
    date_str = db.Column(db.String(10), nullable=False)  # Stored cleanly as 'YYYY-MM-DD'
    genset_id = db.Column(db.String(20), nullable=False)  # Normalized (e.g., 'AG-2')
    total_fuel = db.Column(db.Float, default=0.0)         # Summarized total Gal
    total_load = db.Column(db.Float, default=0.0)         # Summarized total kW
    efficiency = db.Column(db.Float, default=0.0)         # Final calculated Gal/kWh * 1000

    # Guarantees we only ever have ONE row per generator per day
    __table_args__ = (db.UniqueConstraint('date_str', 'genset_id', name='_date_genset_uc'),)

    def __repr__(self):
        return f"<AnalyticsCache {self.date_str} | {self.genset_id}>"
    
    # automatic synchronization method to update cache based on new FuelLog and GeneratorLog entries
    import re
from sqlalchemy import event
from datetime import datetime

# Helper function to parse dates, normalize generator names, and calculate sums
def sync_analytics_for_day(session, report_id, genset_id):
    if not report_id or not genset_id:
        return

    # 1. Look up the master ShiftReport to secure the exact date string
    report = session.query(ShiftReport).filter(
        (ShiftReport.id == report_id) | (ShiftReport.id == str(report_id))
    ).first()
    
    if not report or not report.timestamp:
        return

    # Clean date extraction to 'YYYY-MM-DD'
    if isinstance(report.timestamp, datetime):
        date_str = report.timestamp.strftime('%Y-%m-%d')
    else:
        date_str = str(report.timestamp).strip().split(' ')[0]

    # Normalize generator name (e.g., 'ag2 ' -> 'AG-2')
    match = re.search(r'\d+', str(genset_id))
    normalized_gen = f"AG-{match.group()}" if match else str(genset_id).upper().strip()

    # 2. Gather all shift reports running on this exact date
    sibling_reports = session.query(ShiftReport).filter(ShiftReport.timestamp.like(f"{date_str}%")).all()
    sibling_ids = [r.id for r in sibling_reports] + [str(r.id) for r in sibling_reports]

    # 3. Sum up all fuel entries for this generator on this specific day
    fuel_records = session.query(FuelLog).filter(
        FuelLog.report_id.in_(sibling_ids),
        FuelLog.genset_id.like(f"%{match.group()}%") if match else FuelLog.genset_id == genset_id
    ).all()
    
    total_fuel = 0.0
    for f in fuel_records:
        try:
            cleaned = re.sub(r'[^\d.]', '', str(f.gallons_consumed or 0).strip())
            total_fuel += float(cleaned) if cleaned else 0.0
        except ValueError:
            pass

    # 4. Sum up all load metrics for this generator on this specific day
    gen_records = session.query(GeneratorLog).filter(
        GeneratorLog.report_id.in_(sibling_ids),
        GeneratorLog.genset_id.like(f"%{match.group()}%") if match else GeneratorLog.genset_id == genset_id
    ).all()
    
    total_load = 0.0
    for g in gen_records:
        try:
            cleaned = re.sub(r'[^\d.]', '', str(g.kw or 0).strip())
            total_load += float(cleaned) if cleaned else 0.0
        except ValueError:
            pass

    # 5. Calculate efficiency
    efficiency = (total_fuel / total_load) * 1000 if total_load > 0 else 0.0

    # 6. Check if this record already exists in our summary table
    cache_row = session.query(MonthlyAnalyticsCache).filter_by(date_str=date_str, genset_id=normalized_gen).first()

    # 🛡️ THE FIX: Check the memory queue! 
    # If the database returned nothing, check if a previous hook already created it in memory 
    # seconds ago and is just waiting to be flushed.
    if not cache_row:
        for obj in session.new:
            if isinstance(obj, MonthlyAnalyticsCache) and obj.date_str == date_str and obj.genset_id == normalized_gen:
                cache_row = obj
                break

    # Safely update existing (or pending) row, OR create a brand new one
    if cache_row:
        cache_row.total_fuel = total_fuel
        cache_row.total_load = total_load
        cache_row.efficiency = efficiency
    else:
        new_cache = MonthlyAnalyticsCache(
            date_str=date_str,
            genset_id=normalized_gen,
            total_fuel=total_fuel,
            total_load=total_load,
            efficiency=efficiency
        )
        session.add(new_cache)

# --- HOOK LISTENERS ---
# These listen to all modifications on Fuel and Gen tables and run the sync code automatically
@event.listens_for(FuelLog, 'after_insert')
@event.listens_for(FuelLog, 'after_update')
@event.listens_for(FuelLog, 'after_delete')
def receive_fuel_change(mapper, connection, target):
    object_session = db.object_session(target)
    sync_analytics_for_day(object_session, target.report_id, target.genset_id)

@event.listens_for(GeneratorLog, 'after_insert')
@event.listens_for(GeneratorLog, 'after_update')
@event.listens_for(GeneratorLog, 'after_delete')
def receive_generator_change(mapper, connection, target):
    object_session = db.object_session(target)
    sync_analytics_for_day(object_session, target.report_id, target.genset_id)

# delete after i thinl
def run_historical_backfill():
    """Scans historical logs and builds the new summary baseline table safely."""
    print("🚀 Starting historical analytics backfill...")
    
    # 1. Safety check: prevent running this if data already exists
    existing_count = MonthlyAnalyticsCache.query.count()
    if existing_count > 0:
        print(f"⚠️ Aborted: Cache table already contains {existing_count} records.")
        return

    # 2. Fetch all historical entries
    print("📦 Extracting historical logs from database...")
    fuel_logs = FuelLog.query.all()
    gen_logs = GeneratorLog.query.all()
    
    total_records = len(fuel_logs) + len(gen_logs)
    print(f"📈 Found {len(fuel_logs)} fuel logs and {len(gen_logs)} generator logs to process.")

    # 3. Process Fuel Logs
    for idx, f in enumerate(fuel_logs, 1):
        sync_analytics_for_day(db.session, f.report_id, f.genset_id)
        if idx % 50 == 0 or idx == len(fuel_logs):
            print(f"🔄 Processed {idx}/{len(fuel_logs)} fuel logs...")

    # 4. Process Generator Logs
    for idx, g in enumerate(gen_logs, 1):
        sync_analytics_for_day(db.session, g.report_id, g.genset_id)
        if idx % 50 == 0 or idx == len(gen_logs):
            print(f"🔄 Processed {idx}/{len(gen_logs)} generator logs...")

    # 5. Commit all changes cleanly to powerhouse_enterprise_3.db
    print("💾 Saving structural baseline to database...")
    db.session.commit()
    print("✅ Success! Historical migration complete. Your charts will now load instantly.")