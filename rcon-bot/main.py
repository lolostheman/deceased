import math
import os
import json
import subprocess
import queue
import threading
import time
import re
import platform
from mcrcon import MCRcon
import docker
from queue import Queue, Empty

JOIN_RE = re.compile(
    r"(?:^.*?:\s+)?(?P<player>[A-Za-z0-9_]{3,16}) joined the game",
    re.IGNORECASE
)
LEAVE_RE = re.compile(
    r"(?:^.*?:\s+)?(?P<player>[A-Za-z0-9_]{3,16}) left the game",
    re.IGNORECASE
)
DEATH_RE = re.compile(
    r"""
    ^
    \s*
    (?:^.*?:\s+)?                  # optional log prefix
    (?P<player>[A-Za-z0-9_]{3,16})\s+
    (?P<cause>.+)$                 # rest of message
    """,
    re.IGNORECASE | re.VERBOSE
)

DEATH_CAUSE_RE = re.compile(
    r"""
    (?:
        \bdied\b |
        \bdrowned\b |
        \bexperienced\ kinetic\ energy\b |
        \bintentional\ game\ design\b |
        \bblew\ up\b |
        \bblown\ up\b |
        \bpummeled\b |
        \bkilled\b |
        \bhit\ the\ ground\ too\ hard\b |
        \bfell\b |
        \bleft\ the\ confines\ of\ this\ world\b |
        \bsquish(?:ed|ing)\b |
        \bsuffocat(?:ed|ing)\b |
        \bburn(?:ed|t)\b |
        \bcactus\b |
        \bslain\b |
        \bshot\b |
        \btried\ to\ swim\ in\ lava\b |
        \bfailed\ to\ escape\ the\ Nether\b |
        \bfell\ out\ of\ the\ world\b |
        \bwithered\ away\b |
        \bdiscovered\ the\ void\b |
        \bdiscovered\ the\ floor\ was\ lava\b |
        \bdoomed\b |
        \bstruck\ by\ lightning\b |
        \bpricked\ to\ death\b |
        \bstung\ to\ death\b |
        \bstung\ by\ (?:a\ )?bee\b |
        \bstarved(?:\ to\ death)?\b |
        \bfireballed\b |
        \bblown\ off\ a\ cliff\b |
        \bimpaled\b |
        \bsquashed\b |
        \bwent\ up\ in\ flames\b |
        \bdidn['’]t\ want\ to\ live\b |
        \bskewered\b |
        \bwalked\ into\ fire\b |
        \bwent\ off\ with\ a\ bang\b |
        \bwalked\ into\ the\ danger\ zone\b |
        \bkilled\ by\ magic\b |
        \bfroze\ to\ death\b |
        \bobliterated\b |
        \bannihilat(?:ed|ion)\b |
        \beviscerat(?:ed|ion)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE
)

STATS_RE = re.compile(r":\s*(?:<[^>]+>\s*)?get stats\b", re.IGNORECASE)
SACHIN_RE = re.compile(r":\s*(?:<[^>]+>\s*)?kill southie sachin\b", re.IGNORECASE)
# {"spathak": 1, "xxtenation": 2, "lolostheman": 1}
stop_flag = threading.Event()
RCON_HOST = os.getenv("RCON_HOST", "minecraft")
RCON_PORT = int(os.getenv("RCON_PORT", "25575"))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "change_me_super_secret")
event_q = Queue()
def load_player_json():
    player_names = {}
    try:
        if os.path.exists("/data/player_names.json"):
            with open("/data/player_names.json", "r") as file:
                player_names = json.load(file)
            print("INFO: Player data loaded.")
        else:
            player_names = {}
    except Exception as e:
        print(f"ERROR: Failed to load player data: {e}")
    
    loaded_players = []
    for player, lives in player_names.items():
        loaded_players.append(Player(player, 0.0, lives)) # add ip eventually 
    
    return loaded_players
    
class Player:
    def __init__(self, name, ip = 0.0, cur_deaths = 0): # add ip eventually
        self.name = name
        self.ip = ip
        self.deaths = cur_deaths

    def get_death_count(self):
        return self.deaths

    def add_death(self):
        self.deaths += 1
    
class Server:
    def __init__(self, playerCount = 3, players = None):
        self.playerCount = playerCount
        self.players = players if players is not None else []
        self.maxDeathCount = 3
        self.currentDeathCount = 0
    
    def add_death(self):
        self.currentDeathCount += 1 # need to have logic to detect when game is over
    
    def set_cur_death_count(self):
        for player in self.players:
            self.currentDeathCount += player.get_death_count()

    def set_max_death_count(self):
        self.maxDeathCount = math.floor(len(self.players) * 1)

    def get_max_death_count(self):
        return self.maxDeathCount
    
    def get_death_count(self):
        return self.currentDeathCount
    
    def add_player(self, player):
        if any(p.name == player.name for p in self.players):
            print("Existing player re-joined the server")
            return
        self.players.append(player)
        self.set_max_death_count()
        print("New player joined the server")

def start_minecraft_server():
    """Starts the Minecraft server and logs its output."""
    # Define the command to start the Minecraft server
    minecraft_command = [
        'java',
        "-Dterminal.jline=false",
        "-Djline.terminal=jline.UnsupportedTerminal",
        "-Dlog4j.skipJansi=true",
        '@user_jvm_args.txt', 
        '@libraries/net/minecraftforge/forge/1.20.1-47.4.13/win_args.txt', 
        'nogui' 
    ]

    # Start the Minecraft server process
    process = subprocess.Popen(
        minecraft_command,
        stdin=subprocess.PIPE,  # Allow programmatic input
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        #universal_newlines=True,
        text=True,
        bufsize=1
    )
    return process

def check_for_death(line):
    if ("<" in line and ">" in line) or "[Rcon]" in line:
        return
    
    m = DEATH_RE.match(line)
    if not m:
        return
    
    player_name = m.group("player")
    cause = m.group("cause")
    if DEATH_CAUSE_RE.search(cause):
        event_q.put(("death", player_name, line))


def check_for_join(line):
    m = JOIN_RE.search(line)
    if m:
        player_name = m.group("player")
        event_q.put(("join", player_name, line))

def check_for_stats(line):
    m = STATS_RE.search(line)
    if m:
        event_q.put(("stats", None, line))

def check_for_sachin(line):
    m = SACHIN_RE.search(line)
    if m:
        event_q.put(("sachin", None, line))


def update_player_count(player_name, count):
    if os.path.exists("/data/player_names.json"):
        with open("/data/player_names.json", "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}
    else:
        data = {}
    
    data[player_name] = count
    with open("/data/player_names.json", "w") as f:
        json.dump(data, f, indent=2)

def log_reader():
    proc = subprocess.Popen(
        ['tail', '-F', "/data/logs/latest.log"],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    for line in proc.stdout:
        check_for_death(line)
        check_for_join(line)
        check_for_stats(line)
        check_for_sachin(line)

            
def get_rcon_session():
    """
    Returns a connected MCRcon object.
    Reconnects forever until it succeeds.
    MUST be called from the main thread (because mcrcon uses signal).
    """
    while True:
        try:
            rcon = MCRcon(RCON_HOST, RCON_PASSWORD, port=RCON_PORT)
            rcon.connect()
            print("INFO: RCON connected")
            return rcon
        except Exception as e:
            print(f"WARN: RCON connect failed: {e}. Retrying...")
            time.sleep(2)


def run_game():
    # Load json with player data
    current_players = load_player_json()

    # Load server and set death count stats
    theServer = Server(len(current_players), current_players)
    theServer.set_max_death_count()
    theServer.set_cur_death_count()

    threading.Thread(target=log_reader, daemon=True).start()
    rcon = get_rcon_session()
    while True:
        event, player, line = event_q.get()
        try:
            try:
                if event == "death":
                    print(player, "has died")
                    send_command(rcon, f"say §l§4{player} has fucking died... dumb fuck...§r")
                    time.sleep(5)
                    for p in theServer.players:
                        if p.name == player:
                            p.add_death()
                            update_player_count(player, p.deaths)
                            theServer.add_death()
                            send_command(rcon, f"say §4§l{theServer.currentDeathCount}§r / §4§l{theServer.get_max_death_count()}§r lives wasted...")
                            break
                    if theServer.get_death_count() > theServer.get_max_death_count():
                        send_command(rcon, f"say you guys fucking lost... gg... lightning strike incoming...")
                        time.sleep(3)
                        send_command(rcon, f"say here are some stats, so yall can pick the blame...")
                        time.sleep(1)
                        for p in theServer.players:
                            send_command(rcon, f"say §b§l§n{p.name}§r died §b§l§n{p.deaths}§r time(s)")
                            time.sleep(2)
                        
                        send_command(rcon, "say time to execute log and his friends")
                        time.sleep(3)
                        send_command(rcon, "", ["execute at @a run summon lightning_bolt ~ ~ ~", 
                                        "execute at @a run summon lightning_bolt ~ ~ ~", 
                                        "execute at @a run summon lightning_bolt ~ ~ ~", 
                                        "execute at @a run summon lightning_bolt ~ ~ ~"])
                        time.sleep(1)
                        send_command(rcon, "say 3...")
                        time.sleep(1)
                        send_command(rcon, "say 2...")
                        time.sleep(1)
                        send_command(rcon, "say 1...")
                        time.sleep(1)
                        reset_run()

                        try:
                            rcon.disconnect()
                        except:
                            pass
                        rcon = get_rcon_session()

                        time.sleep(10)
                        # Load json with player data
                        current_players = load_player_json()

                        # Load server and set death count stats
                        theServer = Server(len(current_players), current_players)
                        theServer.set_max_death_count()
                        theServer.set_cur_death_count()
                elif event == "join":
                    print(rcon, f"{player} joined")
                
                    player_exists = False
                    for p in theServer.players:
                        if p.name == player:
                            player_exists = True
                            break
                        
                    if not player_exists:
                        update_player_count(player, 0)
                        theServer.add_player(Player(player))
                        send_command(rcon, f"say {player} has joined")
                        send_command(rcon, f"say The new max Death Count is {theServer.get_max_death_count()}")

                elif event == "stats":
                    send_command(rcon, f"say §4§l{theServer.get_max_death_count() - theServer.currentDeathCount}§r lives remaining")
                    time.sleep(1)
                    for p in theServer.players:
                        send_command(rcon, f"say §b§l§n{p.name}§r died §b§l§n{p.deaths}§r time(s)")
                        time.sleep(2)

                elif event == "sachin":
                    send_command(rcon, f"say §n§6 Sachin now gets punished....§r")
                    time.sleep(2)
                    send_command(rcon, "", ["effect give spathak nausea 20 2 true", 
                                    "effect give spathak slowness 20 2 true", 
                                    "effect give spathak jump_boost 20 3 true"])
            except Exception as e:
                print(f"WARN: RCON error during '{event}': {e}. Reconnecting...")
                try:
                    rcon.disconnect()
                except Exception:
                    pass
                rcon = get_rcon_session()
        finally:
            event_q.task_done()

def main():
    run_game()
        
def send_command(rcon, command, commands=None):
    try:
        if commands is not None:
            for c in commands:
                rcon.command(c)
                time.sleep(0.25)
            return

        if command:
            rcon.command(command)

    except Exception as e:
        # Let the caller handle reconnect
        raise


# def stop_minecraft_server(process):
   
def reset_run():

    client = docker.from_env()
    mc = client.containers.get("mc-bettermc")
    mc.stop(timeout=30)

    """Deletes the Minecraft world folder to reset the world."""
    world_folder = "/data/world" 
    if os.path.exists(world_folder):
        if platform.system() == "Windows":
            os.system(f"rmdir /s /q {world_folder}")
        else:
            os.system(f"rm -rf {world_folder}")
            time.sleep(1)
        print(f"INFO: Deleted the '{world_folder}' folder.")
    else:
        print(f"WARNING: '{world_folder}' folder not found, skipping deletion.")

    if os.path.exists("/data/player_names.json"):
        with open("/data/player_names.json", "w") as f:
            json.dump({}, f)
 
    print("INFO: World + player data reset complete")

    mc.start()

if __name__ == "__main__":
    main()
