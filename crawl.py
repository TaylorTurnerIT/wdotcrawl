import argparse
import sys
import locale
import codecs
import errno
import os
import re
import atexit
import datetime
import subprocess
from wikidot import Wikidot
from rmaint import RepoMaintainer

# TODO: Files.
# TODO: Forum and comment pages.

rawStdout = sys.stdout
rawStderr = sys.stderr
sys.stdout = codecs.getwriter(locale.getpreferredencoding())(sys.stdout.buffer, 'xmlcharrefreplace')
sys.stderr = codecs.getwriter(locale.getpreferredencoding())(sys.stderr.buffer, 'xmlcharrefreplace')

parser = argparse.ArgumentParser(description='Queries Wikidot')
parser.add_argument('site', help='URL of Wikidot site')
# Actions
parser.add_argument('--list-pages', action='store_true', help='List all pages on this site')
parser.add_argument('--source', action='store_true', help='Print page source (requires --page)')
parser.add_argument('--content', action='store_true', help='Print page content (requires --page)')
parser.add_argument('--log', action='store_true', help='Print page revision log (requires --page)')
parser.add_argument('--dump', type=str, help='Download page revisions to this directory')
# Debug actions
parser.add_argument('--list-pages-raw', action='store_true')
parser.add_argument('--log-raw', action='store_true')
# Action settings
parser.add_argument('--page', type=str, help='Query only this page')
parser.add_argument('--depth', type=int, default='10000', help='Query only last N revisions')
parser.add_argument('--revids', action='store_true', help='Store last revision ids in the repository')
# Common settings
parser.add_argument('--debug', action='store_true', help='Print debug info')
parser.add_argument('--delay', type=int, default='200', help='Delay between consequent calls to Wikidot')
args = parser.parse_args()

# ─── Logging setup ───────────────────────────────────────────────────────────

class Tee:
	"""Writes to both the original stream and a log file simultaneously."""
	def __init__(self, stream, logfile):
		self.stream = stream
		self.logfile = logfile

	def write(self, data):
		self.stream.write(data)
		if isinstance(data, bytes):
			data = data.decode('utf-8', 'replace')
		self.logfile.write(data)
		self.logfile.flush()

	def flush(self):
		self.stream.flush()
		self.logfile.flush()

	def __getattr__(self, name):
		return getattr(self.stream, name)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(SCRIPT_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

site_slug = re.sub(r'https?://', '', args.site).split('.')[0]
if args.dump:
	mode_label = 'dump-' + os.path.basename(os.path.abspath(args.dump))
elif args.list_pages:
	mode_label = 'list-pages'
elif args.source:
	mode_label = 'source-' + (args.page or 'unknown')
elif args.log:
	mode_label = 'log-' + (args.page or 'unknown')
else:
	mode_label = 'query'

run_start = datetime.datetime.now()
log_filename = '{}-{}-{}.log'.format(
	site_slug, mode_label, run_start.strftime('%Y%m%d-%H%M%S')
)
log_path = os.path.join(LOGS_DIR, log_filename)
log_file = open(log_path, 'w', encoding='utf-8')

sys.stdout = Tee(sys.stdout, log_file)
sys.stderr = Tee(sys.stderr, log_file)

def _log_footer():
	elapsed = datetime.datetime.now() - run_start
	print('\n=== finished {} | elapsed {} ==='.format(
		datetime.datetime.now().isoformat(timespec='seconds'),
		str(elapsed).split('.')[0],
	))
	# Restore underlying streams before closing log_file so interpreter
	# teardown doesn't flush the Tee into an already-closed file.
	sys.stdout = sys.stdout.stream
	sys.stderr = sys.stderr.stream
	log_file.flush()
	log_file.close()

atexit.register(_log_footer)

print('=== crawl.py started {} ==='.format(run_start.isoformat(timespec='seconds')))
print('site:  {}'.format(args.site))
print('depth: {}'.format(args.depth))
if args.dump:
	print('dump:  {}'.format(args.dump))
if args.page:
	print('page:  {}'.format(args.page))
print('log:   {}'.format(log_path))
print()

# ─── Main ─────────────────────────────────────────────────────────────────────

wd = Wikidot(args.site)
wd.debug = args.debug
wd.delay = args.delay


def force_dirs(path):
	try:
		os.makedirs(path)
	except OSError as exception:
		if exception.errno != errno.EEXIST:
			raise

if args.list_pages_raw:
	print(wd.list_pages_raw(args.depth))

elif args.list_pages:
	for page in wd.list_pages(args.depth):
		print(page)

elif args.source:
	if not args.page:
		raise Exception("Please specify --page for --source.")

	page_id = wd.get_page_id(args.page)
	if not page_id:
		raise Exception("Page not found: "+args.page)

	revs = wd.get_revisions(page_id, 1) # last revision
	print(wd.get_revision_source(revs[0]['id']))

elif args.content:
	if not args.page:
		raise Exception("Please specify --page for --source.")

	page_id = wd.get_page_id(args.page)
	if not page_id:
		raise Exception("Page not found: "+args.page)

	revs = wd.get_revisions(page_id, 1) # last revision
	print(wd.get_revision_version(revs[0]['id']))

elif args.log_raw:
	if not args.page:
		raise Exception("Please specify --page for --log.")

	page_id = wd.get_page_id(args.page)
	if not page_id:
		raise Exception("Page not found: "+args.page)

	print(wd.get_revisions_raw(page_id, args.depth))


elif args.log:
	if not args.page:
		raise Exception("Please specify --page for --log.")

	page_id = wd.get_page_id(args.page)
	if not page_id:
		raise Exception("Page not found: "+args.page)
	for rev in wd.get_revisions(page_id, args.depth):
		print(str(rev))


elif args.dump:
	print("Downloading pages to "+args.dump)
	force_dirs(args.dump)

	# Detect incremental update: read last commit timestamp from existing repo
	since_time = 0
	if os.path.isdir(os.path.join(args.dump, '.git')):
		result = subprocess.run(
			['git', 'log', '-1', '--format=%at'],
			cwd=args.dump, capture_output=True, text=True
		)
		if result.returncode == 0 and result.stdout.strip():
			since_time = int(result.stdout.strip())
			print("Incremental update, last commit: "+str(since_time))

	rm = RepoMaintainer(wd, args.dump)
	rm.debug = args.debug
	rm.storeRevIds = args.revids
	rm.buildRevisionList([args.page] if args.page else None, args.depth, since_time=since_time)

	if not rm.wrevs:
		print("Already up to date.")
	else:
		rm.openRepo()

		print("Downloading revisions...")
		while rm.commitNext():
			pass

		rm.cleanup()
		print("Done.")
