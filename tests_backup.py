import unittest
from unittest.mock import MagicMock, patch
import os
import shutil
import tempfile
import socket
import imaplib
from imap_backup import backup

class TestImapBackupStress(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for backups to avoid polluting the real disk
        self.test_dir = tempfile.mkdtemp()
        self.user = "test@example.com"
        self.password = "password123"
        self.host = "imap.test.com"
        self.port = 993

    def tearDown(self):
        # Clean up temporary directory
        shutil.rmtree(self.test_dir)

    @patch('imaplib.IMAP4_SSL')
    def test_massive_email_count(self, mock_imap_class):
        """Test handling of a massive number of emails (simulating millions)."""
        mock_imap = MagicMock()
        mock_imap_class.return_value = mock_imap

        # Mock Folder Discovery - Use raw bytes for IMAP response
        mock_imap.list.return_value = ("OK", [br'(\HasNoChildren) "/" "INBOX"'])
        
        # Mock status_count (UIDVALIDITY, MESSAGES)
        num_emails = 10000
        mock_imap.status.return_value = ("OK", [b"UIDVALIDITY 123 MESSAGES %d" % num_emails])
        
        # Mock Select
        mock_imap.select.return_value = ("OK", [b"OK [READ-ONLY]"])
        
        # Mock Search: return a string of UIDs
        uids = " ".join(map(str, range(1, num_emails + 1)))
        mock_imap.uid.side_effect = lambda cmd, uid, args: ("OK", [uids]) if cmd == "SEARCH" else ("OK", [(b"1", b"content")])

        res = backup(
            host=self.host, port=self.port, ssl=True, user=self.user, password=self.password,
            dest=self.test_dir, dry_run=False, log=lambda s: None, progress=lambda d, t: None
        )

        self.assertTrue(res["ok"])
        # res["totals"] contains aggregated totals across all folders: {'server': X, 'new': Y, ...}
        self.assertEqual(res["totals"]["server"], num_emails)
        self.assertEqual(res["totals"]["new"], num_emails)

    @patch('imaplib.IMAP4_SSL')
    def test_giant_individual_emails(self, mock_imap_class):
        """Test downloading very large individual emails (e.g., 50MB)."""
        mock_imap = MagicMock()
        mock_imap_class.return_value = mock_imap

        mock_imap.list.return_value = ("OK", [br'(\HasNoChildren) "/" "INBOX"'])
        mock_imap.status.return_value = ("OK", [b"UIDVALIDITY 123 MESSAGES 1"])
        mock_imap.select.return_value = ("OK", [b"OK [READ-ONLY]"])
        mock_imap.uid.side_effect = lambda cmd, uid, args: ("OK", [b"1"]) if cmd == "SEARCH" else ("OK", [(b"1", b"A" * 50 * 1024 * 1024)])

        res = backup(
            host=self.host, port=self.port, ssl=True, user=self.user, password=self.password,
            dest=self.test_dir, dry_run=False, log=lambda s: None, progress=lambda d, t: None
        )

        self.assertTrue(res["ok"])
        expected_file = os.path.join(self.test_dir, self.user, "E-Mails", "Inbox", "1.eml")
        self.assertTrue(os.path.exists(expected_file))
        self.assertEqual(os.path.getsize(expected_file), 50 * 1024 * 1024)

    @patch('imaplib.IMAP4_SSL')
    def test_network_instability_and_resumption(self, mock_imap_class):
        """Test that the tool reconnects and resumes using the SQLite ledger."""
        mock_imap = MagicMock()
        mock_imap_class.return_value = mock_imap

        num_emails = 10
        mock_imap.list.return_value = ("OK", [br'(\HasNoChildren) "/" "INBOX"'])
        mock_imap.status.return_value = ("OK", [b"UIDVALIDITY 123 MESSAGES %d" % num_emails])
        mock_imap.select.return_value = ("OK", [b"OK [READ-ONLY]"])
        
        # We want to trigger a failure on the 5th email
        call_count = 0
        def fetch_side_effect(cmd, uid, args):
            nonlocal call_count
            if cmd == "SEARCH":
                return ("OK", [" ".join(map(str, range(1, num_emails + 1)))])
            call_count += 1
            if call_count == 5:
                raise socket.error("Connection reset by peer")
            return ("OK", [(uid.encode() if isinstance(uid, str) else uid, b"content")])

        mock_imap.uid.side_effect = fetch_side_effect

        # First run: Should fail partway
        res1 = backup(
            host=self.host, port=self.port, ssl=True, user=self.user, password=self.password,
            dest=self.test_dir, dry_run=False, log=lambda s: None, progress=lambda d, t: None
        )
        
        ledger_path = os.path.join(self.test_dir, self.user, f"{self.user}._backup_ledger.sqlite")
        self.assertTrue(os.path.exists(ledger_path))

        # Reset mock to be stable for the second run
        def stable_fetch(cmd, uid, args):
            if cmd == "SEARCH":
                return ("OK", [" ".join(map(str, range(1, num_emails + 1)))])
            return ("OK", [(uid.encode() if isinstance(uid, str) else uid, b"content")])

        mock_imap.uid.side_effect = stable_fetch
        
        res2 = backup(
            host=self.host, port=self.port, ssl=True, user=self.user, password=self.password,
            dest=self.test_dir, dry_run=False, log=lambda s: None, progress=lambda d, t: None
        )
        
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["totals"]["server"], num_emails)
        self.assertEqual(res2["totals"]["new"] + res2["totals"]["skip"], num_emails)

    @patch('imaplib.IMAP4_SSL')
    def test_max_line_length_patch(self, mock_imap_class):
        """Test that the monkey-patch for _MAXLINE prevents crashes on huge response lines."""
        import imaplib
        self.assertEqual(imaplib._MAXLINE, 10_000_000)

if __name__ == "__main__":
    unittest.main()
