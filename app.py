from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import sqlite3
import hashlib
import secrets
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Database initialization
def init_db():
    conn = sqlite3.connect('pairing.db')
    c = conn.cursor()
    
    # Sessions table
    c.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  is_active INTEGER DEFAULT 1)''')
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id TEXT NOT NULL,
                  username TEXT NOT NULL,
                  password_hash TEXT NOT NULL,
                  has_submitted INTEGER DEFAULT 0,
                  FOREIGN KEY (session_id) REFERENCES sessions(id),
                  UNIQUE(session_id, username))''')
    
    # Preferences table
    c.execute('''CREATE TABLE IF NOT EXISTS preferences
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id TEXT NOT NULL,
                  person_from TEXT NOT NULL,
                  person_to TEXT NOT NULL,
                  score INTEGER NOT NULL,
                  submitted_at TEXT NOT NULL,
                  FOREIGN KEY (session_id) REFERENCES sessions(id),
                  UNIQUE(session_id, person_from, person_to))''')
    
    conn.commit()
    conn.close()

init_db()


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def get_db():
    conn = sqlite3.connect('pairing.db')
    conn.row_factory = sqlite3.Row
    return conn


class PairingSystem:
    def __init__(self, session_id):
        self.session_id = session_id
        self.people = self.get_people()
        self.preferences = self.load_preferences()
    
    def get_people(self):
        conn = get_db()
        users = conn.execute(
            'SELECT username FROM users WHERE session_id = ?',
            (self.session_id,)
        ).fetchall()
        conn.close()
        return [u['username'] for u in users]
    
    def load_preferences(self):
        conn = get_db()
        prefs = conn.execute(
            'SELECT person_from, person_to, score FROM preferences WHERE session_id = ?',
            (self.session_id,)
        ).fetchall()
        conn.close()
        
        preferences = {}
        for p in prefs:
            if p['person_from'] not in preferences:
                preferences[p['person_from']] = {}
            preferences[p['person_from']][p['person_to']] = p['score']
        
        return preferences
    
    def calculate_mutual_score(self, person_a, person_b):
        score_ab = self.preferences.get(person_a, {}).get(person_b, 0)
        score_ba = self.preferences.get(person_b, {}).get(person_a, 0)
        return (score_ab + score_ba) / 2
    
    def find_pairs(self):
        available = set(self.people)
        pairs = []
        
        while len(available) >= 2:
            best_pair = None
            best_score = -1
            
            for person_a in available:
                for person_b in available:
                    if person_a == person_b:
                        continue
                    
                    if person_a < person_b:
                        score = self.calculate_mutual_score(person_a, person_b)
                        
                        if score > best_score:
                            best_score = score
                            best_pair = (person_a, person_b)
            
            if best_pair:
                person_a, person_b = best_pair
                pairs.append({
                    'pair': [person_a, person_b],
                    'compatibility': round(best_score, 2),
                    'ratings': {
                        person_a: self.preferences.get(person_a, {}).get(person_b, 0),
                        person_b: self.preferences.get(person_b, {}).get(person_a, 0)
                    }
                })
                available.remove(person_a)
                available.remove(person_b)
        
        unpaired = list(available)[0] if available else None
        return pairs, unpaired
    
    def get_results(self):
        pairs, unpaired = self.find_pairs()
        total_compatibility = sum(p['compatibility'] for p in pairs)
        avg_compatibility = total_compatibility / len(pairs) if pairs else 0
        
        return {
            'pairs': pairs,
            'unpaired': unpaired,
            'total_compatibility': round(total_compatibility, 2),
            'average_compatibility': round(avg_compatibility, 2),
            'num_pairs': len(pairs)
        }


# API Endpoints

@app.route('/api/session/create', methods=['POST'])
def create_session():
    """
    Create a new session with users.
    
    Two modes:
    1. Shared password: {"session_name": "Event", "usernames": ["Alice", "Bob"], "password": "shared123"}
    2. Individual passwords: {"session_name": "Event", "users": [{"username": "Alice", "password": "alice123"}, {"username": "Bob", "password": "bob456"}]}
    """
    data = request.json
    session_name = data.get('session_name')
    
    if not session_name:
        return jsonify({'error': 'session_name required'}), 400
    
    # Mode 1: Simple list with shared password
    if 'usernames' in data:
        usernames = data.get('usernames', [])
        shared_password = data.get('password', 'password123')
        
        if len(usernames) < 2:
            return jsonify({'error': 'At least 2 users required'}), 400
        
        users_data = [{'username': u, 'password': shared_password} for u in usernames]
    
    # Mode 2: Individual passwords
    elif 'users' in data:
        users_data = data.get('users', [])
        
        if len(users_data) < 2:
            return jsonify({'error': 'At least 2 users required'}), 400
        
        for user in users_data:
            if 'username' not in user or 'password' not in user:
                return jsonify({'error': 'Each user must have username and password'}), 400
    
    else:
        return jsonify({'error': 'Either usernames or users array required'}), 400
    
    session_id = secrets.token_urlsafe(16)
    
    conn = get_db()
    try:
        # Create session
        conn.execute(
            'INSERT INTO sessions (id, name, created_at) VALUES (?, ?, ?)',
            (session_id, session_name, datetime.now().isoformat())
        )
        
        # Create users with their passwords
        created_users = []
        for user in users_data:
            username = user['username']
            password_hash = hash_password(user['password'])
            
            conn.execute(
                'INSERT INTO users (session_id, username, password_hash) VALUES (?, ?, ?)',
                (session_id, username, password_hash)
            )
            created_users.append({
                'username': username,
                'password': user['password']  # Include in response for admin to share
            })
        
        conn.commit()
        
        return jsonify({
            'message': 'Session created',
            'session_id': session_id,
            'session_name': session_name,
            'users': created_users
        }), 201
    
    except sqlite3.IntegrityError as e:
        conn.rollback()
        return jsonify({'error': f'Database error: {str(e)}'}), 400
    finally:
        conn.close()


@app.route('/api/login', methods=['POST'])
def login():
    """User login."""
    data = request.json
    session_id = data.get('session_id')
    username = data.get('username')
    password = data.get('password')
    
    if not all([session_id, username, password]):
        return jsonify({'error': 'session_id, username, and password required'}), 400
    
    password_hash = hash_password(password)
    
    conn = get_db()
    user = conn.execute(
        'SELECT * FROM users WHERE session_id = ? AND username = ? AND password_hash = ?',
        (session_id, username, password_hash)
    ).fetchone()
    
    if not user:
        conn.close()
        return jsonify({'error': 'Invalid credentials'}), 401
    
    # Get other users in session
    other_users = conn.execute(
        'SELECT username FROM users WHERE session_id = ? AND username != ?',
        (session_id, username)
    ).fetchall()
    
    conn.close()
    
    return jsonify({
        'message': 'Login successful',
        'username': username,
        'session_id': session_id,
        'has_submitted': bool(user['has_submitted']),
        'other_users': [u['username'] for u in other_users]
    }), 200


@app.route('/api/preferences/submit', methods=['POST'])
def submit_preferences():
    """Submit preferences."""
    data = request.json
    session_id = data.get('session_id')
    username = data.get('username')
    preferences = data.get('preferences', {})
    
    if not all([session_id, username, preferences]):
        return jsonify({'error': 'session_id, username, and preferences required'}), 400
    
    conn = get_db()
    
    try:
        # Delete old preferences
        conn.execute(
            'DELETE FROM preferences WHERE session_id = ? AND person_from = ?',
            (session_id, username)
        )
        
        # Insert new preferences
        for person_to, score in preferences.items():
            if person_to != username and 0 <= score <= 100:
                conn.execute(
                    'INSERT INTO preferences (session_id, person_from, person_to, score, submitted_at) VALUES (?, ?, ?, ?, ?)',
                    (session_id, username, person_to, score, datetime.now().isoformat())
                )
        
        # Mark user as submitted
        conn.execute(
            'UPDATE users SET has_submitted = 1 WHERE session_id = ? AND username = ?',
            (session_id, username)
        )
        
        conn.commit()
        
        # Check if all submitted
        total_users = conn.execute(
            'SELECT COUNT(*) as count FROM users WHERE session_id = ?',
            (session_id,)
        ).fetchone()['count']
        
        submitted_users = conn.execute(
            'SELECT COUNT(*) as count FROM users WHERE session_id = ? AND has_submitted = 1',
            (session_id,)
        ).fetchone()['count']
        
        conn.close()
        
        return jsonify({
            'message': 'Preferences submitted',
            'submitted': submitted_users,
            'total': total_users,
            'all_submitted': submitted_users == total_users
        }), 200
    
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'error': str(e)}), 400


@app.route('/api/results/<session_id>', methods=['GET'])
def get_results(session_id):
    """Get pairing results."""
    conn = get_db()
    
    # Check if all submitted
    total_users = conn.execute(
        'SELECT COUNT(*) as count FROM users WHERE session_id = ?',
        (session_id,)
    ).fetchone()['count']
    
    submitted_users = conn.execute(
        'SELECT COUNT(*) as count FROM users WHERE session_id = ? AND has_submitted = 1',
        (session_id,)
    ).fetchone()['count']
    
    conn.close()
    
    if submitted_users != total_users:
        return jsonify({
            'error': 'Not all preferences submitted',
            'submitted': submitted_users,
            'total': total_users
        }), 400
    
    system = PairingSystem(session_id)
    results = system.get_results()
    
    return jsonify(results), 200


@app.route('/api/session/<session_id>/status', methods=['GET'])
def session_status(session_id):
    """Get session status."""
    conn = get_db()
    
    session = conn.execute(
        'SELECT * FROM sessions WHERE id = ?',
        (session_id,)
    ).fetchone()
    
    if not session:
        conn.close()
        return jsonify({'error': 'Session not found'}), 404
    
    users = conn.execute(
        'SELECT username, has_submitted FROM users WHERE session_id = ?',
        (session_id,)
    ).fetchall()
    
    conn.close()
    
    submitted = sum(1 for u in users if u['has_submitted'])
    
    return jsonify({
        'session_id': session_id,
        'session_name': session['name'],
        'users': [{'username': u['username'], 'has_submitted': bool(u['has_submitted'])} for u in users],
        'submitted': submitted,
        'total': len(users),
        'all_submitted': submitted == len(users)
    }), 200


# Frontend HTML
@app.route('/')
def index():
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <title>Pairing System</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 600px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
        h2 { color: #667eea; margin: 30px 0 20px; font-size: 20px; }
        .subtitle { color: #666; margin-bottom: 30px; }
        input, button {
            width: 100%;
            padding: 12px;
            margin: 8px 0;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 14px;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }
        button {
            background: #667eea;
            color: white;
            border: none;
            cursor: pointer;
            font-weight: 600;
            transition: background 0.3s;
        }
        button:hover { background: #5568d3; }
        button:disabled {
            background: #ccc;
            cursor: not-allowed;
        }
        .hidden { display: none; }
        .slider-container {
            margin: 15px 0;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
        }
        .slider-label {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-weight: 500;
            color: #333;
        }
        .slider-value {
            color: #667eea;
            font-weight: 700;
        }
        input[type="range"] {
            width: 100%;
            height: 6px;
            border-radius: 3px;
            background: #ddd;
            outline: none;
        }
        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none;
            width: 20px;
            height: 20px;
            border-radius: 50%;
            background: #667eea;
            cursor: pointer;
        }
        .result-card {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            margin: 15px 0;
            border-left: 4px solid #667eea;
        }
        .pair-title {
            font-size: 18px;
            font-weight: 600;
            color: #333;
            margin-bottom: 10px;
        }
        .rating-row {
            display: flex;
            justify-content: space-between;
            margin: 5px 0;
            color: #666;
            font-size: 14px;
        }
        .compat-score {
            font-size: 24px;
            font-weight: 700;
            color: #667eea;
            text-align: center;
            margin: 10px 0;
        }
        .status {
            background: #e3f2fd;
            padding: 12px;
            border-radius: 8px;
            margin: 15px 0;
            color: #1976d2;
            text-align: center;
            font-weight: 500;
        }
        .error {
            background: #ffebee;
            color: #c62828;
            padding: 12px;
            border-radius: 8px;
            margin: 15px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Login Screen -->
        <div id="loginScreen">
            <h1>🎯 Pairing System</h1>
            <p class="subtitle">Find your perfect match!</p>
            
            <input type="text" id="sessionId" placeholder="Session ID">
            <input type="text" id="username" placeholder="Username">
            <input type="password" id="password" placeholder="Password">
            <button onclick="login()">Login</button>
            
            <div id="loginError" class="error hidden"></div>
        </div>

        <!-- Preferences Screen -->
        <div id="preferencesScreen" class="hidden">
            <h1>Rate Your Preferences</h1>
            <p class="subtitle">Hi, <strong id="currentUser"></strong>! Rate how much you'd like to pair with each person.</p>
            
            <div id="sliders"></div>
            
            <button onclick="submitPreferences()">Submit Preferences</button>
            <div id="submitStatus" class="status hidden"></div>
        </div>

        <!-- Results Screen -->
        <div id="resultsScreen" class="hidden">
            <h1>✨ Pairing Results</h1>
            <p class="subtitle">Here are the optimal pairs!</p>
            
            <div id="results"></div>
            
            <button onclick="reset()">Back to Login</button>
        </div>
    </div>

    <script>
        let currentSession = null;
        let currentUsername = null;
        let otherUsers = [];

        async function login() {
            const sessionId = document.getElementById('sessionId').value;
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;

            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, username, password })
                });

                const data = await res.json();

                if (!res.ok) {
                    document.getElementById('loginError').textContent = data.error;
                    document.getElementById('loginError').classList.remove('hidden');
                    return;
                }

                currentSession = sessionId;
                currentUsername = username;
                otherUsers = data.other_users;

                document.getElementById('loginScreen').classList.add('hidden');
                
                if (data.has_submitted) {
                    checkResults();
                } else {
                    showPreferences();
                }
            } catch (err) {
                document.getElementById('loginError').textContent = 'Connection error';
                document.getElementById('loginError').classList.remove('hidden');
            }
        }

        function showPreferences() {
            document.getElementById('currentUser').textContent = currentUsername;
            
            const slidersDiv = document.getElementById('sliders');
            slidersDiv.innerHTML = '';

            otherUsers.forEach(user => {
                const div = document.createElement('div');
                div.className = 'slider-container';
                div.innerHTML = `
                    <div class="slider-label">
                        <span>${user}</span>
                        <span class="slider-value" id="value-${user}">50%</span>
                    </div>
                    <input type="range" min="0" max="100" value="50" 
                           id="slider-${user}" 
                           oninput="updateValue('${user}', this.value)">
                `;
                slidersDiv.appendChild(div);
            });

            document.getElementById('preferencesScreen').classList.remove('hidden');
        }

        function updateValue(user, value) {
            document.getElementById(`value-${user}`).textContent = value + '%';
        }

        async function submitPreferences() {
            const preferences = {};
            otherUsers.forEach(user => {
                preferences[user] = parseInt(document.getElementById(`slider-${user}`).value);
            });

            try {
                const res = await fetch('/api/preferences/submit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: currentSession,
                        username: currentUsername,
                        preferences
                    })
                });

                const data = await res.json();

                if (res.ok) {
                    const status = document.getElementById('submitStatus');
                    status.textContent = `✓ Submitted! (${data.submitted}/${data.total} people)`;
                    status.classList.remove('hidden');

                    setTimeout(() => {
                        if (data.all_submitted) {
                            showResults();
                        } else {
                            document.getElementById('preferencesScreen').classList.add('hidden');
                            checkResults();
                        }
                    }, 1500);
                }
            } catch (err) {
                alert('Error submitting preferences');
            }
        }

        async function checkResults() {
            try {
                const res = await fetch(`/api/results/${currentSession}`);
                
                if (res.ok) {
                    showResults();
                } else {
                    const data = await res.json();
                    document.getElementById('preferencesScreen').classList.add('hidden');
                    document.getElementById('resultsScreen').classList.remove('hidden');
                    document.getElementById('results').innerHTML = `
                        <div class="status">
                            Waiting for others... (${data.submitted}/${data.total} submitted)
                        </div>
                    `;
                    setTimeout(checkResults, 3000);
                }
            } catch (err) {
                console.error('Error checking results:', err);
            }
        }

        async function showResults() {
            try {
                const res = await fetch(`/api/results/${currentSession}`);
                const data = await res.json();

                document.getElementById('preferencesScreen').classList.add('hidden');
                document.getElementById('resultsScreen').classList.remove('hidden');

                const resultsDiv = document.getElementById('results');
                resultsDiv.innerHTML = '';

                data.pairs.forEach((pair, i) => {
                    const card = document.createElement('div');
                    card.className = 'result-card';
                    card.innerHTML = `
                        <div class="pair-title">Pair ${i + 1}: ${pair.pair[0]} ↔ ${pair.pair[1]}</div>
                        <!--div class="compat-score">${pair.compatibility}%</div>
                        <div class="rating-row">
                            <span>${pair.pair[0]} → ${pair.pair[1]}</span>
                            <span>${pair.ratings[pair.pair[0]]}%</span>
                        </div>
                        <div class="rating-row">
                            <span>${pair.pair[1]} → ${pair.pair[0]}</span>
                            <span>${pair.ratings[pair.pair[1]]}%</span>
                        </div-->
                    `;
                    resultsDiv.appendChild(card);
                });

                if (data.unpaired) {
                    resultsDiv.innerHTML += `
                        <div class="status">Unpaired: ${data.unpaired}</div>
                    `;
                }

                resultsDiv.innerHTML += `
                    <div class="status">
                        Average Compatibility: ${data.average_compatibility}%
                    </div>
                `;
            } catch (err) {
                console.error('Error loading results:', err);
            }
        }

        function reset() {
            location.reload();
        }
    </script>
</body>
</html>
    ''')


if __name__ == '__main__':
    print("=" * 60)
    print("Pairing System with SQLite Database")
    print("=" * 60)
    print("\nServer starting at: http://localhost:5000")
    print("\nTo create a session, use:")
    print("curl -X POST http://localhost:5000/api/session/create \\")
    print("  -H 'Content-Type: application/json' \\")
    print("  -d '{\"session_name\": \"Team Event\", \"usernames\": [\"Alice\", \"Bob\", \"Charlie\", \"Diana\"], \"password\": \"test123\"}'")
    print("\n" + "=" * 60 + "\n")
    
    app.run(debug=True, host='0.0.0.0', port=5010)