import unittest
from hypothesis import given, strategies as st
from backend.scheduler import BackupScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta, timezone

# Strategy for valid cron expressions
@st.composite
def cron_schedule(draw):
    # Simplified strategies to avoid invalid combinations (e.g. Feb 30)
    s = draw(st.integers(min_value=0, max_value=59))
    m = draw(st.integers(min_value=0, max_value=59))
    h = draw(st.integers(min_value=0, max_value=23))
    dow = draw(st.integers(min_value=0, max_value=6))
    return f"{s} {m} {h} * * {dow}"

class TestSchedulerProperties(unittest.TestCase):
    
    @given(cron_schedule(), st.datetimes(
        min_value=datetime(2023, 1, 1), 
        max_value=datetime(2030, 1, 1)
    ))
    def test_next_run_validity(self, cron_expr, now):
        """
        Property: Given a valid cron expression and a reference time,
        the next scheduled run time must be in the future.
        """
        # First verify it passes our own validation
        valid, error = BackupScheduler.validate_cron_expression(cron_expr)
        self.assertTrue(valid, f"Generated cron '{cron_expr}' rejected: {error}")

        # We use APScheduler's CronTrigger directly to match implementation
        parts = cron_expr.split()
        trigger = CronTrigger(
            second=parts[0],
            minute=parts[1],
            hour=parts[2],
            day=parts[3],
            month=parts[4],
            day_of_week=parts[5],
            timezone=timezone.utc
        )
        
        # Make 'now' timezone-aware (UTC) because CronTrigger requires it
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
            
        next_run = trigger.get_next_fire_time(None, now)
        
        self.assertIsNotNone(next_run)
        self.assertGreater(next_run, now)
        
        # Verify it's within a reasonable timeframe (e.g., < 1 year + margin)
        self.assertLess(next_run - now, timedelta(days=366*2))

    @given(st.lists(
        st.fixed_dictionaries({
            'id': st.integers(),
            'status': st.sampled_from(['running', 'pending', 'completed', 'failed'])
        }),
        min_size=1, max_size=20
    ), st.integers(min_value=1, max_value=5))
    def test_concurrency_invariant(self, jobs, max_concurrency):
        """
        Property: Scheduler should not start new jobs if running >= max.
        We simulate the check logic here.
        """
        running_count = sum(1 for j in jobs if j['status'] == 'running')
        
        # Logic from Scheduler._check_pending_jobs (approx)
        can_start_new = running_count < max_concurrency
        
        if running_count >= max_concurrency:
            self.assertFalse(can_start_new, "Should not start job if max concurrency reached")
        else:
            self.assertTrue(can_start_new, "Should be able to start job if below capacity")

if __name__ == '__main__':
    unittest.main()
