from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
import threading
import os

app = app = Flask(__name__, template_folder='templates2')
app.config['SECRET_KEY'] = 'pong-secret-key'
socketio = SocketIO(app, cors_allowed_origins='*')

CANVAS_W = 800
CANVAS_H = 500
PADDLE_W = 12
PADDLE_H = 80
BALL_R = 8
PADDLE_SPEED = 6
BALL_SPEED = 5

rooms = {}
rooms_lock = threading.Lock()


def make_code():
    return ''.join(random.choices(string.ascii_uppercase, k=4))


def new_ball():
    angle = random.uniform(-0.5, 0.5)
    direction = 1 if random.random() > 0.5 else -1
    return {
        'x': CANVAS_W / 2,
        'y': CANVAS_H / 2,
        'vx': direction * BALL_SPEED,
        'vy': BALL_SPEED * angle
    }


def new_room(win_score=5):
    return {
        'players': {},
        'ball': new_ball(),
        'scores': [0, 0],
        'player_order': [],
        'running': False,
        'winner': None,
        'win_score': win_score
    }


@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('create_room')
def on_create_room(data):
    win_score = int(data.get('win_score', 5))
    win_score = max(1, min(win_score, 21))  # clamp 1–21
    code = make_code()
    with rooms_lock:
        rooms[code] = new_room(win_score)
    emit('room_created', {'code': code, 'player': 1, 'win_score': win_score})


@socketio.on('join_room')
def on_join_room(data):
    code = data.get('code', '').upper()
    with rooms_lock:
        room = rooms.get(code)
        if not room:
            emit('error', {'message': 'Room not found'})
            return
        if len(room['players']) >= 2:
            emit('error', {'message': 'Room is full'})
            return
        room['players'][request.sid] = {
            'paddle_y': CANVAS_H / 2 - PADDLE_H / 2,
            'input': None
        }
        room['player_order'].append(request.sid)
        win_score = room['win_score']
    join_room(code)
    emit('room_joined', {'code': code, 'player': 2, 'win_score': win_score})
    if len(room['players']) == 2:
        room['running'] = True
        emit('game_start', to=code)
        start_game_loop(code)


@socketio.on('paddle_input')
def on_paddle_input(data):
    code = data.get('room')
    with rooms_lock:
        room = rooms.get(code)
        if room and request.sid in room['players']:
            room['players'][request.sid]['input'] = data.get('direction')


@socketio.on('rematch')
def on_rematch():
    with rooms_lock:
        for code, room in rooms.items():
            if request.sid in room['players']:
                room['scores'] = [0, 0]
                room['ball'] = new_ball()
                room['winner'] = None
                room['running'] = True
                for p in room['players'].values():
                    p['paddle_y'] = CANVAS_H / 2 - PADDLE_H / 2
                    p['input'] = None
                emit('game_start', to=code)
                start_game_loop(code)
                break


@socketio.on('disconnect')
def on_disconnect():
    with rooms_lock:
        for code, room in rooms.items():
            if request.sid in room['players']:
                del room['players'][request.sid]
                room['running'] = False
                room['player_order'] = [s for s in room['player_order'] if s != request.sid]
                if room['player_order']:
                    emit('opponent_left', {'message': 'Opponent left!'}, to=code)
                break


def start_game_loop(code):
    def loop():
        import time
        while True:
            with rooms_lock:
                room = rooms.get(code)
                if not room or not room['running']:
                    return

                win_score = room['win_score']

                for sid, p in room['players'].items():
                    if p['input'] == 'up':
                        p['paddle_y'] = max(0, p['paddle_y'] - PADDLE_SPEED)
                    elif p['input'] == 'down':
                        p['paddle_y'] = min(CANVAS_H - PADDLE_H, p['paddle_y'] + PADDLE_SPEED)

                ball = room['ball']
                ball['x'] += ball['vx']
                ball['y'] += ball['vy']

                event = None

                if ball['y'] - BALL_R <= 0:
                    ball['y'] = BALL_R
                    ball['vy'] = abs(ball['vy'])
                    event = 'wall'
                elif ball['y'] + BALL_R >= CANVAS_H:
                    ball['y'] = CANVAS_H - BALL_R
                    ball['vy'] = -abs(ball['vy'])
                    event = 'wall'

                p1 = room['player_order'][0] if len(room['player_order']) > 0 else None
                p2 = room['player_order'][1] if len(room['player_order']) > 1 else None

                if p1 and p1 in room['players']:
                    py = room['players'][p1]['paddle_y']
                    if (ball['vx'] < 0 and
                        ball['x'] - BALL_R <= PADDLE_W and
                        ball['x'] - BALL_R > PADDLE_W - 20 and
                        py <= ball['y'] <= py + PADDLE_H):
                        ball['x'] = PADDLE_W + BALL_R
                        ball['vx'] = abs(ball['vx']) * 1.05
                        ball['vy'] += (ball['y'] - (py + PADDLE_H / 2)) * 0.1
                        event = 'paddle'

                if p2 and p2 in room['players']:
                    py = room['players'][p2]['paddle_y']
                    if (ball['vx'] > 0 and
                        ball['x'] + BALL_R >= CANVAS_W - PADDLE_W and
                        ball['x'] + BALL_R < CANVAS_W - PADDLE_W + 20 and
                        py <= ball['y'] <= py + PADDLE_H):
                        ball['x'] = CANVAS_W - PADDLE_W - BALL_R
                        ball['vx'] = -abs(ball['vx']) * 1.05
                        ball['vy'] += (ball['y'] - (py + PADDLE_H / 2)) * 0.1
                        event = 'paddle'

                if ball['x'] < -BALL_R:
                    room['scores'][1] += 1
                    event = 'score'
                    if room['scores'][1] >= win_score:
                        room['running'] = False
                        room['winner'] = 2
                    else:
                        room['ball'] = new_ball()
                elif ball['x'] > CANVAS_W + BALL_R:
                    room['scores'][0] += 1
                    event = 'score'
                    if room['scores'][0] >= win_score:
                        room['running'] = False
                        room['winner'] = 1
                    else:
                        room['ball'] = new_ball()

                state = {
                    'ball': {'x': ball['x'], 'y': ball['y']},
                    'paddles': {},
                    'scores': room['scores'],
                    'event': event
                }
                for i, sid in enumerate(room['player_order']):
                    if sid in room['players']:
                        state['paddles'][i] = room['players'][sid]['paddle_y']

            if room['winner']:
                emit('game_over', {'winner': room['winner'], 'scores': room['scores']}, to=code)
                return
            emit('game_state', state, to=code)
            time.sleep(1 / 60)

    socketio.start_background_task(loop)


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))   
