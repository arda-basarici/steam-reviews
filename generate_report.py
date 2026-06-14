"""generate_report.py — build the PDF report "What the Score Doesn't Tell You".

Run from steam-reviews/:  python generate_report.py
Reads the processed parquet, regenerates print-quality figures, and assembles a
narrative PDF report at the project root. The report is its own piece of writing —
not an export of the notebooks — telling one story across the four findings.
"""
from __future__ import annotations
import os, tempfile
import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                PageBreak, Table, TableStyle, Flowable)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

import report_charts as rc

# --- palette (mirrors the visual identity) ---
ACCENT   = colors.HexColor("#C0392B")
INK      = colors.HexColor("#2B2B2B")
GREYDARK = colors.HexColor("#7F8C8D")
GREY     = colors.HexColor("#BDC3C7")
GREYLITE = colors.HexColor("#ECF0F1")
WHITE    = colors.white

DATA_REVIEWS = "data/processed/reviews.parquet"
DATA_META    = "data/processed/metadata.parquet"
OUT_PDF      = "steam_review_report.pdf"

# ---------------------------------------------------------------- styles
def styles():
    s = getSampleStyleSheet()
    out = {}
    out["title"] = ParagraphStyle("title", parent=s["Title"], fontName="Helvetica-Bold",
        fontSize=30, leading=34, textColor=INK, spaceAfter=6, alignment=TA_LEFT)
    out["subtitle"] = ParagraphStyle("subtitle", parent=s["Normal"], fontName="Helvetica",
        fontSize=14, leading=19, textColor=GREYDARK, spaceAfter=22, alignment=TA_LEFT)
    out["h2"] = ParagraphStyle("h2", parent=s["Heading2"], fontName="Helvetica-Bold",
        fontSize=17, leading=21, textColor=INK, spaceBefore=14, spaceAfter=2)
    out["kicker"] = ParagraphStyle("kicker", parent=s["Normal"], fontName="Helvetica-Bold",
        fontSize=10, leading=13, textColor=ACCENT, spaceAfter=2, alignment=TA_LEFT)
    out["body"] = ParagraphStyle("body", parent=s["Normal"], fontName="Helvetica",
        fontSize=10.7, leading=15.5, textColor=INK, spaceAfter=9, alignment=TA_LEFT)
    out["thesis"] = ParagraphStyle("thesis", parent=s["Normal"], fontName="Helvetica-Bold",
        fontSize=12, leading=17, textColor=ACCENT, spaceBefore=4, spaceAfter=12,
        leftIndent=10, borderPadding=0)
    out["caption"] = ParagraphStyle("caption", parent=s["Normal"], fontName="Helvetica-Oblique",
        fontSize=8.5, leading=11, textColor=GREYDARK, spaceBefore=2, spaceAfter=16, alignment=TA_CENTER)
    out["cover_tag"] = ParagraphStyle("cover_tag", parent=s["Normal"], fontName="Helvetica",
        fontSize=11, leading=17, textColor=INK, spaceAfter=4)
    out["foot"] = ParagraphStyle("foot", parent=s["Normal"], fontName="Helvetica",
        fontSize=9, leading=13, textColor=GREYDARK)
    out["tbl_h"] = ParagraphStyle("tbl_h", parent=s["Normal"], fontName="Helvetica-Bold",
        fontSize=9.5, leading=12, textColor=WHITE)
    out["tbl_c"] = ParagraphStyle("tbl_c", parent=s["Normal"], fontName="Helvetica",
        fontSize=9.5, leading=12.5, textColor=INK)
    out["tbl_cb"] = ParagraphStyle("tbl_cb", parent=s["Normal"], fontName="Helvetica-Bold",
        fontSize=9.5, leading=12.5, textColor=INK)
    return out

class HRule(Flowable):
    def __init__(self, width, color=GREY, thickness=0.8):
        super().__init__(); self.width=width; self.color=color; self.thickness=thickness
    def draw(self):
        self.canv.setStrokeColor(self.color); self.canv.setLineWidth(self.thickness)
        self.canv.line(0,0,self.width,0)

class AccentBand(Flowable):
    """A short crimson rule with breathing room, drawn below the heading so it
    never collides with descenders."""
    def __init__(self, width=2.2*cm, gap_above=5, gap_below=4, thickness=3):
        super().__init__()
        self.width = width; self.thickness = thickness
        self.gap_above = gap_above; self.gap_below = gap_below
        self.height = thickness + gap_above + gap_below
    def wrap(self, availWidth, availHeight):
        return (self.width, self.height)
    def draw(self):
        self.canv.setStrokeColor(ACCENT)
        self.canv.setLineWidth(self.thickness)
        y = self.gap_below + self.thickness/2
        self.canv.line(0, y, self.width, y)

def accent_band(width=2.2*cm):
    return AccentBand(width=width)

# ---------------------------------------------------------------- load
def load():
    reviews = pd.read_parquet(DATA_REVIEWS)
    meta = pd.read_parquet(DATA_META)
    return reviews, meta

# ---------------------------------------------------------------- numbers
def compute_facts(reviews):
    r = reviews
    f = {}
    f["n"] = len(r)
    f["games"] = r["app_id"].nunique()
    f["langs"] = r["language"].nunique()
    f["overall"] = r["voted_up"].mean()*100
    sub2 = r["playtime_at_review"] < 120
    f["sub2_rate"] = r.loc[sub2,"voted_up"].mean()*100
    f["over2_rate"] = r.loc[~sub2,"voted_up"].mean()*100
    ref = r["refunded"]==True
    f["ref_rate"] = r.loc[ref,"voted_up"].mean()*100
    rec = r["voted_up"]
    after = (r["playtime_forever"]-r["playtime_at_review"]).clip(lower=0)/60
    f["after_rec"] = after[rec].median()
    f["after_non"] = after[~rec].median()
    f["stop_non"] = (after[~rec] < 1).mean()*100
    ln = r["review"].str.len().fillna(0)
    f["len_pos"] = ln[rec].median(); f["len_neg"] = ln[~rec].median()
    pub = r[r["num_games_owned"]>=1]
    f["vet_small"] = pub.loc[pub["num_games_owned"]<10,"voted_up"].mean()*100
    f["vet_big"] = pub.loc[pub["num_games_owned"]>=200,"voted_up"].mean()*100
    return f

# ---------------------------------------------------------------- build
def build(reviews, meta, tmpdir):
    S = styles()
    PW = A4[0] - 4*cm           # content width
    story = []
    P = lambda t, st="body": story.append(Paragraph(t, S[st]))
    SP = lambda h: story.append(Spacer(1, h))
    f = compute_facts(reviews)

    def figure(fn, name, caption, w=PW):
        path = os.path.join(tmpdir, name)
        fn(reviews, path)
        # scale image to content width, preserve aspect; slightly shorter to keep
        # each section on a single page
        story.append(Image(path, width=w, height=w*0.50))
        story.append(Paragraph(caption, S["caption"]))

    # ============ COVER ============
    SP(2.2*cm)
    story.append(accent_band(3.2*cm)); SP(14)
    P("What the Score<br/>Doesn't Tell You", "title")
    P(f"A behavioral analysis of {f['n']:,} Steam reviews", "subtitle")
    SP(8)
    P("Every game on Steam wears a single number — <b>85% positive</b>, say — and we read it "
      "as a verdict. This report takes that number apart. Across a quarter-million reviews, the "
      "same headline score turns out to hide <i>when</i> a player reviewed, <i>whether they stayed</i>, "
      "<i>how they wrote</i>, and <i>who they were</i>. Four findings, each tested the same demanding way, "
      "each a layer the score quietly compresses away.", "cover_tag")
    SP(20)
    for tag, txt in [
        ("THE REFUND WINDOW", "Reviews written before the two-hour refund deadline are far harsher — a cliff right at the line."),
        ("A REVIEW IS A GOODBYE", "Pan a game and you stop playing it; recommend one and you keep going. The score encodes which."),
        ("NEGATIVITY IS VERBOSE", "Negative reviews run more than twice as long. Brevity, it turns out, is a positive act."),
        ("THE VETERAN IS HARSHER", "The same game scores lower from players with bigger libraries. Experience raises the bar."),
    ]:
        story.append(Paragraph(tag, S["kicker"]))
        story.append(Paragraph(txt, S["body"]))
    SP(16)
    story.append(HRule(PW))
    SP(6)
    P('github.com/arda-basarici/ai-journey &nbsp;·&nbsp; Data from Steam\'s public review API', "foot")
    story.append(PageBreak())

    # ============ EXECUTIVE SUMMARY ============
    P("The number everyone trusts", "h2")
    story.append(accent_band(2.2*cm)); SP(6)
    P("A Steam rating looks like a verdict: a single percentage, the crowd's thumbs up or down, "
      "distilled. It is one of the most consulted numbers in gaming — players buy or skip on the "
      "strength of it. But a percentage is a <i>compression</i>. It flattens hundreds of thousands of "
      "individual decisions, each made by a particular person at a particular moment, into one digit "
      "of signal. This report decompresses it.")
    P(f"The data is {f['n']:,} reviews across {f['games']} games, in {f['langs']} languages — collected "
      "from Steam's public API, cleaned, and analysed not in the aggregate but <i>within each game</i>, "
      "so that a pattern has to prove itself title by title before we believe it. That discipline is the "
      "spine of everything here: it is the difference between a real effect and a trick of which games "
      "happened to land in the sample. Four patterns survived it. Here is the short version.")
    SP(6)

    # summary table
    rows = [
        [Paragraph("What we looked at", S["tbl_h"]), Paragraph("What the score was hiding", S["tbl_h"])],
        [Paragraph("<b>When</b> the review was written", S["tbl_cb"]),
         Paragraph(f"Below the 2-hour refund line, recommendation falls to ~{f['sub2_rate']:.0f}% — "
                   f"against ~{f['over2_rate']:.0f}% after. A cliff at the deadline.", S["tbl_c"])],
        [Paragraph("<b>Whether</b> the player stayed", S["tbl_cb"]),
         Paragraph(f"Recommenders play a median {f['after_rec']:.0f}h more after reviewing; panners play "
                   f"~0h. {f['stop_non']:.0f}% of negative reviewers never return.", S["tbl_c"])],
        [Paragraph("<b>How</b> the review was written", S["tbl_cb"]),
         Paragraph(f"Negative reviews run {f['len_neg']:.0f} characters to positives' {f['len_pos']:.0f} — "
                   f"more than twice as long. Brevity signals approval.", S["tbl_c"])],
        [Paragraph("<b>Who</b> was reviewing", S["tbl_cb"]),
         Paragraph(f"Players with 200+ games recommend at ~{f['vet_big']:.0f}%, the smallest libraries at "
                   f"~{f['vet_small']:.0f}%. The same game, a different verdict.", S["tbl_c"])],
    ]
    t = Table(rows, colWidths=[PW*0.32, PW*0.68])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),INK),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE, GREYLITE]),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
        ("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),
        ("LINEBELOW",(0,0),(-1,-2),0.4,GREY),
    ]))
    story.append(t)
    SP(10)
    P("None of these is visible in the rating itself. Each is a behaviour the number averages into "
      "silence. The rest of this report takes them one at a time — moving from the mechanical to the "
      "human: from a refund clock, to the moment a player walks away, to the shape of what they write, "
      "to who they are when they write it.")
    story.append(PageBreak())

    # ============ §1 WHEN — REFUND ============
    P("1 · When — the refund window", "h2")
    story.append(accent_band(2.2*cm)); SP(6)
    P("It is late, you have owned the game ninety minutes, and it is not clicking. Steam will refund "
      "anything under two hours played — so you have a decision to make, and a deadline to make it by. "
      "Quietly take the money back, or leave a warning on the way out. That small, ordinary moment turns "
      "out to leave a mark across the entire dataset.")
    figure(rc.fig_refund_gradient, "f_refund.png",
           f"Recommend rate climbs steeply through the sub-two-hour buckets, then locks flat. "
           f"{f['n']:,} reviews across {f['games']} games.")
    P(f"Sort reviewers by how long they had played when they wrote, and the pattern is stark. A reviewer "
      f"who played under thirty minutes recommends the game barely half the time. Cross the two-hour line "
      f"and the rate jumps to a steady ~{f['over2_rate']:.0f}% — and then stays there, flat, whether the "
      f"player logs ten more hours or a thousand. The score doesn't drift with enjoyment; it steps, sharply, "
      f"right at the refund boundary.")
    P("And this isn't a couple of angry games dragging an average around. The pattern holds <i>within</i> "
      "almost every individual game in the set, and an entirely separate signal — Steam's own flag for "
      "reviews that were actually refunded — points the same way: those reviewers cluster inside the window "
      f"and recommend at just ~{f['ref_rate']:.0f}%. A multivariate model agrees, with playtime the "
      "strongest predictor of a positive review even after the game, the review's length, and its votes are "
      "all controlled for.")
    story.append(Paragraph("Reviewers who write before the refund window closes are far harsher than those who write after.", S["thesis"]))
    P("What we can't say for certain is <i>why</i>. Perhaps the deadline itself manufactures angry reviews — "
      "play briefly, warn the world, reclaim your money. Or perhaps players who were never going to like the "
      "game simply quit early, so short playtime and negativity share a cause rather than one driving the "
      "other. Observational data can't fully separate the two. But the sharpness of the cliff — a clean break "
      "at exactly two hours, not a gentle slope — leans toward the window doing real work. Whatever the hand, "
      "the fingerprint is unmistakable.")
    story.append(PageBreak())

    # ============ §2 WHETHER — GOODBYE ============
    P("2 · Whether they stayed — the goodbye", "h2")
    story.append(accent_band(2.2*cm)); SP(6)
    P("A review reads like a verdict handed down from outside the game. But it is written from <i>inside</i> "
      "a relationship with it — at some particular moment, with more of that relationship still to come, or "
      "not. Because the data records both a reviewer's total playtime and their playtime at the moment they "
      "wrote, we can see what they did next. The answer is quietly poignant.")
    figure(rc.fig_goodbye, "f_goodbye.png",
           "Left: median hours played after the review. Right: who walked away versus who stuck around.")
    P(f"Players who recommend a game keep playing it — a median of {f['after_rec']:.0f} more hours after they "
      f"review. Players who pan it mostly stop: their median additional playtime is essentially zero. Around "
      f"{f['stop_non']:.0f}% of negative reviewers never meaningfully return to the game after reviewing it. "
      f"The negative review isn't a pause for feedback partway through; it is one of the last things the player "
      f"does before leaving.")
    story.append(Paragraph("A recommendation is a love letter written mid-relationship. A pan is a goodbye note on the way out.", S["thesis"]))
    P("The striking part is that words and behaviour agree, with no need to read a single sentence of the "
      "review. The sentiment a player typed predicts what they physically did next — keep playing, or close "
      "the game for good. And like everything here, it holds title by title, not just in the pile: inside "
      "almost every game, its recommenders go on to play longer than its detractors. The score, it turns out, "
      "quietly encodes whether the reviewer stayed.")
    story.append(PageBreak())

    # ============ §3 HOW — LENGTH ============
    P("3 · How they wrote — negativity is verbose", "h2")
    story.append(accent_band(2.2*cm)); SP(6)
    P("Think of the last time something delighted you, and the last time something let you down. Which "
      "reaction took more words? On Steam the answer is not close — and you can see it before reading a "
      "single review, in nothing but their length.")
    figure(rc.fig_length, "f_length.png",
           f"Median review length by sentiment. Negative reviews run {f['len_neg']:.0f} characters to positives' {f['len_pos']:.0f}.", w=PW*0.74)
    P(f"A positive review has a median length of {f['len_pos']:.0f} characters — about the size of "
      f"\u201cgreat game, highly recommend.\u201d A negative one runs {f['len_neg']:.0f}, more than twice as long. "
      f"The effect is statistically overwhelming, and it holds within nearly every game. But the interesting "
      f"part is <i>where</i> the gap lives. It isn't that a few furious players write enormous screeds — the "
      f"median ignores those. It's the opposite end: the one-line review, the bare \u201c10/10,\u201d the curt "
      f"\u201cgreat game,\u201d is almost always a recommendation. Over nine in ten ultra-short reviews are positive.")
    story.append(Paragraph("A positive review can be a reflex. A negative one tends to be an argument.", S["thesis"]))
    P("Why disapproval reaches for more words is a genuine open question — and an honest report should leave "
      "it open. Maybe complaint feels a burden of proof that praise never has to carry: \u201cI loved it\u201d is a "
      "complete review, while \u201cI hated it\u201d invites the question <i>why</i>. Maybe anger is simply more "
      "verbose, or maybe complaints name specific failures that take room to describe. Telling those apart "
      "would mean reading the words themselves, not just counting them — which is exactly where this project "
      "goes next. For now the asymmetry stands on its own, worth sitting with.")
    story.append(PageBreak())

    # ============ §4 WHO — VETERAN ============
    P("4 · Who they were — the veteran is harsher", "h2")
    story.append(accent_band(2.2*cm)); SP(6)
    P("The first three findings were about the review. This one is about the reviewer. Steam attaches to "
      "each review a rough measure of experience — how many games the author owns — and experience, it turns "
      "out, shows up in the scores. This is the gentlest of the four findings, and it comes wrapped in honest "
      "caveats, so it is offered plainly rather than oversold.")
    figure(rc.fig_veteran, "f_veteran.png",
           "Recommend rate by the reviewer's library size, public profiles only.", w=PW*0.78)
    P(f"Among reviewers whose profiles are public, recommendation falls cleanly as libraries grow — from the "
      f"~{f['vet_small']:.0f}% of the smallest collections to ~{f['vet_big']:.0f}% for those owning two hundred "
      f"games or more. The natural worry is that this is just selection: a player rich in games and time tries "
      f"everything, including the dross, while a ten-game owner buys only sure things. If that were the whole "
      f"story, though, the effect would vanish once we compare reviewers of the <i>same</i> game — and it "
      f"doesn't. Title by title, the player with hundreds of games behind them is harder to please.")
    story.append(Paragraph("Experience raises the bar. The more of a medium you've seen, the less easily a new entry impresses you.", S["thesis"]))
    P("It is a familiar truth — the seasoned film critic, the lifelong reader, harder to win over than the "
      "newcomer — now visible in a quarter-million game reviews. The same game earns a different verdict "
      "depending on who is holding the controller. Which is, in the end, the whole point of this report: the "
      "score is not a property of the game alone.")
    story.append(PageBreak())

    # ============ THE TURN — WHAT WE DIDN'T USE ============
    P("The things we didn't claim", "h2")
    story.append(accent_band(2.2*cm)); SP(6)
    P("A report is only as trustworthy as the findings it was willing to throw away. We tested roughly a "
      "dozen ideas; four survived. The rest were set aside on purpose, and the most interesting of them is "
      "worth showing — because it looks like a finding and isn't.")
    figure(rc.fig_reviewbomb, "f_bomb.png",
           "Helldivers 2's daily recommend rate climbs out of a deep dip — but our window begins near its bottom.")
    P("For two days in May 2026, Helldivers 2's daily recommend rate sat near 27%, far below its usual ~85%, "
      "before climbing back over the following month. It has every appearance of a review bomb. And yet we "
      "decline to call it one — because a review bomb is a claim about <i>anomaly</i>, and to prove a spike is "
      "unusual you need the game's ordinary history to compare against. Our collection reaches back only weeks; "
      "it captures the recovery but never the calm before. We can show the dip. We cannot prove how strange it "
      "was. So we say the smaller, true thing, and leave the larger claim alone.")
    P("Several other tempting patterns met the same fate. Pricier games looked slightly worse-reviewed, but on "
      "only forty-odd titles the signal was too fragile to trust. Critics and players turned out to broadly "
      "<i>agree</i> — and agreement is no story. Different language communities showed different rates, but "
      "tangled inextricably with which games they play. Free copies produced almost exactly the same sentiment "
      "as paid ones — a clean null. And Steam's built-in \u201chelpfulness\u201d score sat at its default value "
      "for three-quarters of all reviews, hollow at the core. None became a chapter. Naming them is what earns "
      "the four that did.")
    story.append(PageBreak())

    # ============ CLOSE ============
    P("What the score really is", "h2")
    story.append(accent_band(2.2*cm)); SP(6)
    P("Set the four findings side by side and a single idea emerges. The headline rating is not a verdict on a "
      "game so much as a <i>compression</i> of human behaviour — of when people reviewed, whether they stayed, "
      "how they wrote, and who they were. Decompress it and the structure underneath is legible, consistent, "
      "and often surprising: a refund clock bending sentiment, a goodbye encoded in playtime, approval hiding "
      "in brevity, a bar that rises with experience.")
    P("Two honest limits point the way forward. Several questions went unanswered for want of <i>history</i> — "
      "our window is recent by design, which a fuller crawl would extend. And the deepest question of all — "
      "<i>why</i> a negative review reaches for more words — went unanswered because we deliberately studied "
      "the <i>shape</i> of reviews, not their <i>content</i>. We counted words; we never read them. That is the "
      "next phase of this project: turning from structure to language, and asking how much the words reveal that "
      "the numbers around them cannot.")
    story.append(Paragraph("What the score doesn't tell you, this report found in the structure around it. The rest is in the words.", S["thesis"]))
    SP(10)
    story.append(HRule(PW)); SP(6)
    P("Methods, source code, and the full analysis notebooks: github.com/arda-basarici/ai-journey &nbsp;·&nbsp; "
      "Findings are validated within individual games; all figures regenerated from the processed dataset.", "foot")

    return story

def main():
    reviews, meta = load()
    with tempfile.TemporaryDirectory() as tmp:
        doc = SimpleDocTemplate(OUT_PDF, pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm, topMargin=1.8*cm, bottomMargin=1.8*cm,
            title="What the Score Doesn't Tell You", author="arda-basarici")
        story = build(reviews, meta, tmp)
        doc.build(story)
    print(f"wrote {OUT_PDF}")

if __name__ == "__main__":
    main()