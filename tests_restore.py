import unittest
from unittest.mock import patch
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import gmail_upload

class TestRestoreAutomation(unittest.TestCase):
    def setUp(self):
        # Path to developer's backup
        self.src = "/Users/gproductionsinc./Documents/EAHI knowledge node/08 - Resources/developer@equgruppo.com"

    def test_discovery_and_parsing(self):
        """Verify that discover_backup and the parsers find all contacts, events, and tasks."""
        self.assertTrue(os.path.isdir(self.src), f"Backup source directory {self.src} not found!")

        # 1. Test discover_backup
        counts = gmail_upload.discover_backup(self.src)
        print("Discovered counts:", counts)
        self.assertEqual(counts["contacts"], 20, "Should find 20 contacts")
        self.assertEqual(counts["calendar"], 1, "Should find 1 calendar .ics file")
        self.assertEqual(counts["tasks"], 2, "Should find 2 task .ics files")

        # 2. Gather files
        contacts_files = []
        calendar_files = []
        tasks_files = []
        for root, dirs, files in os.walk(self.src):
            low = root.lower()
            for f in files:
                path = os.path.join(root, f)
                if f.lower().endswith(".vcf"):
                    contacts_files.append(path)
                elif f.lower().endswith(".ics"):
                    if "task" in low:
                        tasks_files.append(path)
                    else:
                        calendar_files.append(path)

        # 3. Test parsers
        log_fn = lambda s: print("Parser log:", s)
        parsed_contacts = gmail_upload.parse_vcards_list(contacts_files, log_fn)
        parsed_events = gmail_upload.parse_events_list(calendar_files, log_fn)
        parsed_tasks = gmail_upload.parse_tasks_list(tasks_files, log_fn)

        print(f"Parsed {len(parsed_contacts)} contacts, {len(parsed_events)} events, {len(parsed_tasks)} tasks")
        
        # Verify contact contents
        self.assertEqual(len(parsed_contacts), 20)
        # Check first contact structure
        first_contact_path, first_contact = parsed_contacts[0]
        self.assertIn("uid", first_contact)
        self.assertIn("fn", first_contact)
        self.assertIn("emails", first_contact)

        # Verify event contents
        self.assertEqual(len(parsed_events), 1)
        first_event_path, first_event = parsed_events[0]
        self.assertIn("summary", first_event)
        self.assertIn("uid", first_event)
        self.assertIn("start", first_event)

        # Verify task contents
        self.assertEqual(len(parsed_tasks), 2)
        # Verify specific fields in parsed tasks
        task_summaries = [t["summary"] for p, t in parsed_tasks]
        self.assertIn("test", task_summaries)
        self.assertIn("test2", task_summaries)

        task_statuses = [t["status"] for p, t in parsed_tasks]
        self.assertIn("completed", task_statuses)
        self.assertIn("needsAction", task_statuses)

    def test_dry_run_restore(self):
        """Verify that upload() in dry_run mode completes successfully and prints counts."""
        log_msgs = []
        def log_fn(s):
            log_msgs.append(s)
            print(s)

        tokens = ("dummy_access_token", "dummy_refresh_token")
        res = gmail_upload.upload(
            user="developer@equgruppo.com",
            tokens=tokens,
            src=self.src,
            dry_run=True,
            log=log_fn
        )

        self.assertTrue(res["ok"])
        self.assertEqual(res["note"], "dry_run")
        # Check that dry run printed summaries
        summary_log = "".join(log_msgs)
        self.assertIn("Contacts: 20", summary_log)
        self.assertIn("Calendar events: 1", summary_log)
        self.assertIn("Tasks: 2", summary_log)

    @patch('gmail_upload._upload_contact')
    @patch('gmail_upload._upload_event')
    @patch('gmail_upload._upload_task')
    @patch('gmail_upload._upload_email')
    def test_restore_ledger_and_resumption(self, mock_email, mock_task, mock_event, mock_contact):
        """Verify that a real run creates the ledger, records items, and resumes correctly."""
        import tempfile
        import shutil
        import sqlite3
        
        # Create a temporary directory structure mimicking the backup directory
        tmp_dir = tempfile.mkdtemp()
        try:
            # Create subdirs
            os.makedirs(os.path.join(tmp_dir, "Address book"))
            os.makedirs(os.path.join(tmp_dir, "Calendar"))
            os.makedirs(os.path.join(tmp_dir, "Tasks"))
            os.makedirs(os.path.join(tmp_dir, "E-Mails"))
            
            # Write a dummy contact
            with open(os.path.join(tmp_dir, "Address book", "contact1.vcf"), "w") as f:
                f.write("BEGIN:VCARD\nVERSION:3.0\nFN:John Doe\nUID:john-doe-uid-123\nEND:VCARD\n")
                
            # Write a dummy calendar event
            with open(os.path.join(tmp_dir, "Calendar", "event1.ics"), "w") as f:
                f.write("BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Meeting\nUID:meeting-uid-123\nDTSTART:20260626T100000Z\nEND:VEVENT\nEND:VCALENDAR\n")
                
            # Write a dummy task
            with open(os.path.join(tmp_dir, "Tasks", "task1.ics"), "w") as f:
                f.write("BEGIN:VCALENDAR\nBEGIN:VTODO\nSUMMARY:Task1\nUID:task-uid-123\nSTATUS:NEEDS-ACTION\nEND:VTODO\nEND:VCALENDAR\n")
                
            # Write a dummy email
            with open(os.path.join(tmp_dir, "E-Mails", "email1.eml"), "w") as f:
                f.write("Subject: Test Email\n\nHello World\n")
                
            # Set up mock upload results
            mock_contact.return_value = True
            mock_event.return_value = True
            mock_task.return_value = True
            mock_email.return_value = True
            
            # Run restore the first time
            tokens = ("dummy_access_token", "dummy_refresh_token")
            res1 = gmail_upload.upload(
                user="developer@equgruppo.com",
                tokens=tokens,
                src=tmp_dir,
                dry_run=False,
                log=lambda s: None
            )
            
            self.assertTrue(res1["ok"])
            self.assertEqual(res1["total_restored"], 4)
            
            # Verify ledger database exists and contains entries
            ledger_path = os.path.join(tmp_dir, "_restore_ledger.sqlite")
            self.assertTrue(os.path.exists(ledger_path))
            
            conn = sqlite3.connect(ledger_path)
            rows = conn.execute("SELECT kind, key FROM restored").fetchall()
            conn.close()
            
            kinds = [r[0] for r in rows]
            self.assertIn("contact", kinds)
            self.assertIn("calendar", kinds)
            self.assertIn("task", kinds)
            self.assertIn("email", kinds)
            
            # Verify mock call counts: each called exactly once
            self.assertEqual(mock_contact.call_count, 1)
            self.assertEqual(mock_event.call_count, 1)
            self.assertEqual(mock_task.call_count, 1)
            self.assertEqual(mock_email.call_count, 1)
            
            # Reset mocks
            mock_contact.reset_mock()
            mock_event.reset_mock()
            mock_task.reset_mock()
            mock_email.reset_mock()
            
            # Run restore the second time: all should be skipped because of ledger
            res2 = gmail_upload.upload(
                user="developer@equgruppo.com",
                tokens=tokens,
                src=tmp_dir,
                dry_run=False,
                log=lambda s: None
            )
            
            self.assertTrue(res2["ok"])
            self.assertEqual(res2["total_restored"], 0)
            self.assertEqual(res2["stats"]["contacts"]["skip"], 1)
            self.assertEqual(res2["stats"]["events"]["skip"], 1)
            self.assertEqual(res2["stats"]["tasks"]["skip"], 1)
            self.assertEqual(res2["stats"]["emails"]["skip"], 1)
            
            # Verify mock call counts are zero
            self.assertEqual(mock_contact.call_count, 0)
            self.assertEqual(mock_event.call_count, 0)
            self.assertEqual(mock_task.call_count, 0)
            self.assertEqual(mock_email.call_count, 0)
            
        finally:
            shutil.rmtree(tmp_dir)

if __name__ == "__main__":
    unittest.main()
