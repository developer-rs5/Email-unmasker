import re
import smtplib
import dns.resolver
import time
import os
import socket
import threading
from collections import deque, defaultdict
from itertools import product
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TimeRemainingColumn, TextColumn
from rich.text import Text
from pyfiglet import Figlet
import argparse
from flask import Flask, render_template_string, request, redirect, url_for
from flask_socketio import SocketIO

# Configuration
CHARSET = 'abcdefghijklmnopqrstuvwxyz0123456789'
MAX_THREADS = 500
UNVERIFIABLE_DOMAINS = ['gmail.com', 'googlemail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'protonmail.com']
SMTP_TIMEOUT = 5
VALID_EMAILS_FILE = "results/valid-emails.txt"
MAX_DISPLAY_EMAILS = 500
SOCIAL_LINKS = {
    "Discord": "https://discord.zenuxs.xyz",
    "Instagram": "https://instagram.com/developer.rs",
    "GitHub": "https://github.com/developer-rs5"
}

console = Console()
app = Flask(__name__)
socketio = SocketIO(app)

# Shared state for web interface
results_state = {
    'emails': [],
    'valid_emails': [],
    'progress': 0,
    'total': 0,
    'running': False
}

# MX Record Cache with thread safety
mx_cache = {}
cache_lock = threading.Lock()

def animated_banner():
    fig = Figlet(font='slant')
    title = fig.renderText('EMAIL UNMASKER')
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print("[bold yellow]Developed by: [green]developer.rs[/green][/bold yellow]")
    
    # Display social links
    console.print("\n[bold]Connect with us:[/bold]")
    for platform, url in SOCIAL_LINKS.items():
        console.print(f"[blue]{platform}:[/blue] [link={url}]{url}[/link]")
    console.print()

def generate_emails(masked):
    prefix, domain = masked.split('@')
    positions = [i for i, c in enumerate(prefix) if c == '*']
    known = [c if c != '*' else None for c in prefix]

    total = len(CHARSET) ** len(positions)
    console.print(f"[cyan]Generating {total} combinations...[/cyan]")
    for combo in product(CHARSET, repeat=len(positions)):
        temp = known[:]
        for pos, char in zip(positions, combo):
            temp[pos] = char
        yield ''.join(temp) + '@' + domain

# Optimized MX record lookup with caching
def get_mx_records(domain):
    with cache_lock:
        if domain in mx_cache:
            return mx_cache[domain]
        
        try:
            mx_records = dns.resolver.resolve(domain, 'MX')
            sorted_records = sorted(mx_records, key=lambda x: x.preference)
            mx_list = [str(r.exchange).rstrip('.') for r in sorted_records]
            mx_cache[domain] = mx_list
            return mx_list
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            mx_cache[domain] = []
            return []
        except dns.resolver.Timeout:
            return []
        except Exception as e:
            print(f"DNS error for {domain}: {str(e)}")
            return []

# Optimized email verification
def verify_email_with_mx(email, mx_host):
    try:
        with smtplib.SMTP(mx_host, 25, timeout=SMTP_TIMEOUT) as server:
            server.set_debuglevel(0)
            server.ehlo()
            server.mail('<>')
            code, _ = server.rcpt(email)
            return code in (250, 251)
    except (smtplib.SMTPException, socket.error):
        return False
    except Exception as e:
        print(f"SMTP error for {email}@{mx_host}: {str(e)}")
        return False

def is_valid_email(email):
    try:
        parts = email.split('@')
        if len(parts) != 2:
            return False
            
        domain = parts[1]
        mx_hosts = get_mx_records(domain)
        if not mx_hosts:
            return False
            
        # Try up to 2 highest priority MX servers
        for host in mx_hosts[:2]:
            if verify_email_with_mx(email, host):
                return True
                
        return False
        
    except Exception as e:
        print(f"Validation error for {email}: {str(e)}")
        return False

def update_web_interface(email, status, valid_count, progress, total):
    try:
        with app.test_request_context():
            socketio.emit('update', {
                'email': email,
                'status': status,
                'valid_count': valid_count,
                'progress': progress,
                'total': total
            })
    except Exception as e:
        print(f"Web interface update failed: {str(e)}")
         
def run_verification(masked, threads):
    global results_state
    results_state['running'] = True 
    results_state['emails'] = []
    results_state['valid_emails'] = []
    
    os.makedirs("results", exist_ok=True)
    
    # Pre-resolve domains
    domain = masked.split('@')[1]
    console.print(f"[yellow]Pre-resolving MX records for {domain}...[/yellow]")
    mx_hosts = get_mx_records(domain)
    
    if not mx_hosts:
        console.print(f"[red]No MX records found for {domain}. Aborting verification.[/red]")
        results_state['running'] = False
        return
        
    console.print(f"[green]Found {len(mx_hosts)} MX records for {domain}[/green]")
    
    # Generate emails and get total count
    prefix = masked.split('@')[0]
    num_stars = prefix.count('*')
    total = len(CHARSET) ** num_stars
    results_state['total'] = total
    
    if total > 1000000:  # 1 million
        console.print(f"[red]Warning: {total} emails to verify - this may take significant time[/red]")
        if console.input("Continue? (y/n): ").strip().lower() != 'y':
            results_state['running'] = False
            return
    
    start_time = time.time()
    checked_count = 0
    valid_emails = set()
    seen_emails = set()

    # Create progress bar
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
    )
    task = progress.add_task("Checking...", total=total)
    
    # Create results display using deque
    last_results = deque(maxlen=MAX_DISPLAY_EMAILS)
    results_display = Text("", no_wrap=True)
    results_panel = Panel(results_display, title="Results", border_style="blue")
    
    # Create main display
    main_layout = Panel(
        Panel(progress, title="Progress", border_style="green"),
        title="Email Unmasker", 
        border_style="bold magenta"
    )
    
    with Live(main_layout, refresh_per_second=10, console=console) as live:
        with ThreadPoolExecutor(max_workers=min(threads, MAX_THREADS)) as executor:
            # Submit tasks in chunks to avoid memory overload
            batch_size = 10000
            email_gen = generate_emails(masked)
            futures = {}
            
            while True:
                batch = [next(email_gen, None) for _ in range(batch_size)]
                if not any(batch):
                    break
                    
                for email in batch:
                    if email is None:
                        continue
                    future = executor.submit(is_valid_email, email)
                    futures[future] = email
                    
                for future in as_completed(futures.copy()):
                    email = futures.pop(future)
                    if email in seen_emails:
                        continue
                    seen_emails.add(email)

                    try:
                        valid = future.result()
                        status = "✅ Valid" if valid else "❌ Invalid"
                        color = "green" if valid else "red"
                        
                        # Update results display
                        last_results.appendleft(f"[{color}]{email} - {status}[/]")
                        results_display = Text("\n".join(last_results), no_wrap=True)
                        results_panel.renderable = results_display
                        
                        # Update web interface
                        if valid:
                            valid_emails.add(email)
                            results_state['valid_emails'].append(email)
                        
                        results_state['emails'].append({'email': email, 'status': status})
                        checked_count += 1
                        progress_percent = int((checked_count / total) * 100)
                        
                        update_web_interface(
                            email=email,
                            status=status,
                            valid_count=len(valid_emails),
                            progress=progress_percent,
                            total=total
                        )

                        # Update progress
                        progress.update(task, advance=1)
                        
                        # Refresh display
                        live.update(main_layout)

                    except Exception as e:
                        last_results.appendleft(f"[yellow]{email} - ⚠️ Error ({str(e)})[/]")
                        results_display = Text("\n".join(last_results), no_wrap=True)
                        results_panel.renderable = results_display
                        results_state['emails'].append({'email': email, 'status': "⚠️ Error"})
                        progress_percent = int((checked_count / total) * 100)
                        update_web_interface(email, "⚠️ Error", len(valid_emails), progress_percent, total)
                        progress.update(task, advance=1)
                        live.update(main_layout)

    if valid_emails:
        with open(VALID_EMAILS_FILE, "w") as f:
            for email in sorted(valid_emails):
                f.write(email + "\n")
        box = "\n".join(sorted(valid_emails))
        console.print(Panel(box, title="✅ Valid Emails Found", border_style="green"))
    else:
        console.print(Panel("No valid emails found.", title="❌ Result", border_style="red"))
    
    elapsed = time.time() - start_time
    console.print(f"⏱️ Verification completed in {elapsed:.2f} seconds")
    results_state['running'] = False


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        masked = request.form['masked']
        threads = int(request.form['threads'])
        
        # Start verification in a separate thread
        threading.Thread(
            target=run_verification,
            args=(masked, threads),
            daemon=True
        ).start()
        
        return redirect(url_for('live_results'))
    
    return render_template_string('''
        <html>
        <head>
            <title>Email Unmasker</title>
            <style>
                body { font-family: Arial, sans-serif; background: #1a1a1a; color: #f0f0f0; text-align: center; }
                input, button { padding: 10px; margin: 10px; font-size: 1em; }
                button { background-color: #28a745; color: white; border: none; cursor: pointer; }
                h2 { color: #00ffcc; }
                .social-links { margin-top: 20px; }
                .social-links a { margin: 0 10px; color: #00ccff; text-decoration: none; }
                .warning { color: #ff9900; margin: 10px 0; }
            </style>
        </head>
        <body>
            <h2>🔍 Email Unmasker Web</h2>
            <form method="post">
                <label>Masked Email:</label><br>
                <input name="masked" placeholder="r****r@example.com" required><br>
                <label>Threads:</label><br>
                <input name="threads" type="number" value="50" min="1" max="100" required><br>
                <button type="submit">Start</button>
            </form>
            
            <div class="warning">
                <p>Note: Verification may take time for large combinations</p>
            </div>
            
            <div class="social-links">
                <p>Connect with us:</p>
                <a href="https://discord.zenuxs.xyz" target="_blank">Discord</a>
                <a href="https://instagram.com/developer.rs" target="_blank">Instagram</a>
                <a href="https://github.com/developer-rs5" target="_blank">GitHub</a>
            </div>
            
            <p>Developed by <b>developer.rs</b> | CLI + Web | SMTP-based email validation</p>
        </body>
        </html>
    ''')

@app.route('/live-results')
def live_results():
    return render_template_string('''
        <html>
        <head>
            <title>Live Results</title>
            <style>
                body { font-family: monospace; background: #111; color: #eee; padding: 20px; }
                #results { height: 70vh; overflow-y: auto; border: 1px solid #444; padding: 10px; }
                .valid { color: #0f0; }
                .invalid { color: #f00; }
                .error { color: #ff0; }
                .progress-container { width: 100%; background-color: #333; margin: 10px 0; }
                .progress-bar { height: 20px; background-color: #28a745; width: 0%; }
                .stats { margin: 10px 0; }
                .timer { margin-top: 10px; color: #aaa; }
            </style>
            <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
            <script>
                const socket = io();
                let startTime = Date.now();
                
                function updateTimer() {
                    const elapsed = Math.floor((Date.now() - startTime) / 1000);
                    document.getElementById('timer').textContent = `Elapsed: ${elapsed}s`;
                }
                
                setInterval(updateTimer, 1000);
                
                socket.on('update', function(data) {
                    // Update results list
                    const resultsDiv = document.getElementById('results');
                    const entry = document.createElement('div');
                    entry.className = data.status.includes('Valid') ? 'valid' : 
                                      data.status.includes('Invalid') ? 'invalid' : 'error';
                    entry.textContent = `${data.email} - ${data.status}`;
                    resultsDiv.appendChild(entry);
                    resultsDiv.scrollTop = resultsDiv.scrollHeight;
                    
                    // Update progress
                    document.getElementById('progress-bar').style.width = `${data.progress}%`;
                    document.getElementById('progress-text').textContent = `${data.progress}%`;
                    
                    // Update stats
                    document.getElementById('valid-count').textContent = data.valid_count;
                    document.getElementById('checked-count').textContent = Math.round(data.total * (data.progress/100));
                    document.getElementById('total-count').textContent = data.total;
                });
            </script>
        </head>
        <body>
            <h2>Live Results</h2>
            
            <div class="stats">
                <div>Valid Emails: <span id="valid-count">0</span></div>
                <div>Progress: <span id="checked-count">0</span>/<span id="total-count">0</span></div>
                <div class="timer" id="timer">Elapsed: 0s</div>
            </div>
            
            <div class="progress-container">
                <div id="progress-bar" class="progress-bar"></div>
            </div>
            <div id="progress-text" style="text-align: center;">0%</div>
            
            <div id="results"></div>
            
            <a href="/" style="color: #00ccff;">← Back to Home</a>
        </body>
        </html>
    ''')

def cli_entry():
    parser = argparse.ArgumentParser(description='Email Unmasker by developer.rs')
    parser.add_argument('-e', '--email', help='Masked email (e.g. r****r@example.com)')
    parser.add_argument('-t', '--threads', help='Threads count (default: 50)', type=int, default=50)
    parser.add_argument('--web', help='Launch web interface', action='store_true')
    args = parser.parse_args()

    if args.web:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False)
    elif args.email:
        if not re.match(r'^[a-z0-9.*]+@[a-z]+\.[a-z]+$', args.email):
            console.print("[red]❌ Invalid email format[/red]")
            return
        run_verification(args.email.strip().lower(), args.threads)
    else:
        animated_banner()
        while True:
            masked = console.input("Enter masked email (e.g. r******s@example.com): ").strip().lower()
            if re.match(r'^[a-z0-9.*]+@[a-z]+\.[a-z]+$', masked):
                break
            console.print("[red]❌ Invalid email format[/red]")
        
        while True:
            try:
                threads = int(console.input("How many threads (recommended 50-100)?: "))
                if 1 <= threads <= 500:
                    break
                console.print("[red]❌ Thread count must be between 1-500[/red]")
            except ValueError:
                console.print("[red]❌ Invalid number[/red]")
        
        run_verification(masked, threads)

if __name__ == "__main__":
    cli_entry()