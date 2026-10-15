"""The notification slots and their words, as agreed in the mockup (section 7).

Seeded once, then edited on screen. The wording is carried over from the Flask app's rotating
pool where it fits — it is the best-written thing in that app, and the voice is the product.
"""
from .models import NotifyLine, NotifySlot

S = NotifyLine.Situation

SLOTS = [
    {
        'key': NotifySlot.Key.MORNING, 'at': '08:00', 'mode': NotifySlot.Mode.ALWAYS, 'order': 1,
        'topic': 'Where the cycle stands today',
        'lines': {
            S.RUNNING_LOGGED: [
                'Day {day} of your period. Go gently today where you can.',
                'Day {day}. Rest where you get the chance.',
                'Day {day} of your period — be kind to yourself today.',
            ],
            S.RUNNING_UNLOGGED: [
                'Day {day}, if it is still going. Tap today in when you get a moment.',
                'Still going? One tap keeps your calendar right.',
                'Day {day} by our count — add today when you can.',
            ],
            S.DUE_TODAY: [
                'It may start today. Nothing to do — log it when it does.',
                'Today is the day we expect it. No rush.',
            ],
            S.DUE_SOON: [
                'Likely in {days} days. Maybe keep a pad in your bag today.',
                'About {days} days to go — PMS may show up soon. Be kind to yourself.',
            ],
            S.QUIET: [
                'Day {day}, and about {days} days to go. An easy stretch.',
                'Taking it one day at a time — day {day} of your cycle today.',
                'No rush today — your next period is still about {days} days away.',
                'Everything looks steady. Day {day} of {cycle}.',
            ],
            S.LATE_EARLY: [
                '{late} days late so far. Often nothing, but worth keeping an eye on.',
                'Running {late} days late. Bodies do this; keep an eye on it.',
            ],
            S.LATE_LONG: [
                '{late} days late now. If that is unusual for you, it may be worth a check.',
                'It has been {late} days. Worth a word with someone if this is unusual.',
            ],
            S.OVULATION: [
                'Day {day} — around ovulation, going by your average.',
            ],
            S.LOW_STOCK: [
                'Also low: {items}.',
            ],
        },
    },
    {
        'key': NotifySlot.Key.AFTERNOON, 'at': '13:30', 'mode': NotifySlot.Mode.NEWS, 'order': 2,
        'topic': 'Practical only — stock, or today not logged',
        'lines': {
            S.RUNNING_UNLOGGED: [
                'Still bleeding today? One tap keeps your calendar right.',
                'Today is not in yet — a tap now and it is done.',
            ],
            S.LOW_STOCK: [
                '{items} — worth picking some up on the way home.',
                'Running low on {items}. Might be time to restock.',
            ],
        },
    },
    {
        'key': NotifySlot.Key.EVENING, 'at': '21:00', 'mode': NotifySlot.Mode.NEWS, 'order': 3,
        'topic': 'Winding down — log before bed, or a heads-up',
        'lines': {
            S.RUNNING_LOGGED: [
                'Day {day} done. A warm bottle and an early night do more than anything.',
                'That is day {day} behind you. Sleep well.',
            ],
            S.RUNNING_UNLOGGED: [
                'Today is not logged yet — add it before bed?',
                'One tap before you sleep keeps tomorrow simple.',
            ],
            S.DUE_TODAY: [
                'Nothing logged today. If it has not started, that is fine — tomorrow is still early.',
            ],
            S.DUE_SOON: [
                'It could start tomorrow. Everything is ready in the app.',
            ],
            S.LATE_EARLY: [
                'If it started today and you have not logged it, tap it in while you remember.',
            ],
            S.LOW_STOCK: [
                '{items} are low. Add them to tomorrow’s list?',
            ],
        },
    },
    {
        'key': NotifySlot.Key.NIGHT, 'at': '22:00', 'mode': NotifySlot.Mode.NEWS, 'order': 4,
        'topic': 'Spare slot, off by default',
        'enabled': False,
        'lines': {},
    },
]


def ensure_slots():
    """Create the slots and their lines if they are not there yet. Safe to call repeatedly."""
    made = 0
    for spec in SLOTS:
        slot, created = NotifySlot.objects.get_or_create(
            key=spec['key'],
            defaults={'at': spec['at'], 'mode': spec['mode'], 'order': spec['order'],
                      'topic': spec['topic'], 'is_enabled': spec.get('enabled', True)},
        )
        if not created:
            continue
        made += 1
        NotifyLine.objects.bulk_create([
            NotifyLine(slot=slot, situation=situation, text=text)
            for situation, texts in spec['lines'].items() for text in texts
        ])
    return made
