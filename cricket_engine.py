class Batter:
    def __init__(self):
        self.runs = 0
        self.balls = 0

class Bowler:
    def __init__(self):
        self.balls = 0
        self.runs = 0
        self.wickets = 0

class Match:
    def __init__(self):
        self.total = 0
        self.wickets = 0
        self.balls = 0
        self.striker = Batter()
        self.bowler = Bowler()

    def add_runs(self, r):
        self.striker.runs += r
        self.striker.balls += 1
        self.bowler.balls += 1
        self.bowler.runs += r
        self.total += r
        self.balls += 1

    def wide(self, r):
        self.total += r + 1
        self.bowler.runs += r + 1

    def noball(self, r):
        self.total += r + 1
        self.bowler.runs += r + 1

    def wicket(self):
        self.wickets += 1
        self.bowler.wickets += 1
        self.balls += 1