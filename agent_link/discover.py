"""`hubctl discover`: pick servers from ~/.ssh/config in a checklist (Space to pick, Enter to apply)."""
import glob
import locale
import os
from pathlib import Path

WILDCARDS = set('*?!')


def ssh_hosts(path=None, _seen=None):
    """Concrete `Host` aliases (no patterns) in SSH config order, with their HostName if set.

    Follows `Include` (relative paths are under ~/.ssh) and skips `Match` blocks.
    """
    path = Path(path or Path.home() / '.ssh' / 'config').expanduser()
    seen = _seen if _seen is not None else set()
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    hosts, current = [], []
    for raw in path.read_text(errors='replace').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, _, value = line.replace('=', ' ', 1).partition(' ')
        key, value = key.lower(), value.strip()
        if key == 'include':
            for pattern in value.split():
                pattern = os.path.expanduser(pattern)
                if not os.path.isabs(pattern):
                    pattern = str(Path.home() / '.ssh' / pattern)
                for included in sorted(glob.glob(pattern)):
                    hosts.extend(ssh_hosts(included, seen))
        elif key == 'host':
            current = []
            for alias in value.split():
                if not WILDCARDS & set(alias):
                    current.append({'alias': alias, 'hostname': ''})
            hosts.extend(current)
        elif key == 'match':
            current = []
        elif key == 'hostname':
            for host in current:
                host['hostname'] = host['hostname'] or value
    unique, names = [], set()
    for host in hosts:
        if host['alias'] not in names:
            names.add(host['alias'])
            unique.append(host)
    return unique


class Picker:
    """Checklist state, separate from drawing so it can be tested without a terminal."""

    def __init__(self, hosts, registered):
        self.hosts, self.registered = hosts, set(registered)
        self.cursor, self.selected = 0, set()

    def move(self, step):
        if self.hosts:
            self.cursor = (self.cursor + step) % len(self.hosts)

    def toggle(self):
        if not self.hosts:
            return
        alias = self.hosts[self.cursor]['alias']
        if alias in self.registered:
            return
        self.selected ^= {alias}

    def chosen(self):
        return [h['alias'] for h in self.hosts if h['alias'] in self.selected]

    def lines(self):
        for index, host in enumerate(self.hosts):
            alias = host['alias']
            mark = '[=]' if alias in self.registered else '[x]' if alias in self.selected else '[ ]'
            note = '  (등록됨)' if alias in self.registered else ''
            target = f'  {host["hostname"]}' if host['hostname'] and host['hostname'] != alias else ''
            yield index == self.cursor, f'{mark} {alias}{target}{note}'


HELP = '↑↓/j k 이동   Space 선택   Enter 완료   q 취소'


def pick(hosts, registered):
    """Run the checklist. Returns the chosen aliases, or None when cancelled."""
    import curses
    locale.setlocale(locale.LC_ALL, '')
    picker = Picker(hosts, registered)

    def run(screen):
        curses.curs_set(0)
        screen.keypad(True)
        top = 0
        while True:
            height, width = screen.getmaxyx()
            rows = max(1, height - 4)
            top = min(max(top, picker.cursor - rows + 1), picker.cursor)
            screen.erase()
            screen.addnstr(0, 0, f'연결할 서버를 고르세요 ({len(picker.selected)}개 선택)', width - 1, curses.A_BOLD)
            screen.addnstr(1, 0, HELP, width - 1)
            for row, (current, text) in enumerate(list(picker.lines())[top:top + rows]):
                screen.addnstr(row + 3, 0, ('> ' if current else '  ') + text, width - 1,
                               curses.A_REVERSE if current else curses.A_NORMAL)
            screen.refresh()
            key = screen.getch()
            if key in (curses.KEY_UP, ord('k')):
                picker.move(-1)
            elif key in (curses.KEY_DOWN, ord('j')):
                picker.move(1)
            elif key == ord(' '):
                picker.toggle()
            elif key in (curses.KEY_ENTER, 10, 13):
                return picker.chosen()
            elif key == ord('q'):  # not ESC: it also starts arrow-key sequences
                return None

    return curses.wrapper(run)
