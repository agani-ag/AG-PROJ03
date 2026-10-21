"""Turn each module's own people into one hub Member apiece — merging the overlap.

    python manage.py merge_identities --dry-run          a report, changes nothing
    python manage.py merge_identities --apply
    python manage.py merge_identities --apply --only pocket

Why a command and not a data migration: migrations are gitignored in this project and never
reach the server, so this is the only way a data move can be run there — the same reason
`uppercase_names` and `carry_syncup_to_hub` exist.

**Run the dry run first, and read it.** It says how many people match by email, how many by
phone, who is only in one module, and — the part worth your attention — which pairs it refuses
to decide on its own. Take a backup from /download/sqlite before `--apply`.

Safe to run twice: it links whatever is unlinked and leaves everything else alone.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.auth import normalise_phone
from core.models import Member

MODULES = ('hibiscus', 'pocket')


def _rows(which):
    if which == 'hibiscus':
        from hibiscus.models import Person
        return Person.objects.all().order_by('pk')
    from pocket.models import Account
    return Account.objects.all().order_by('pk')


def _keys(row):
    """Every handle this row can be recognised by — an email, a phone, or both.

    Both, not one: somebody can be in the diary by email and in the money book by phone, and a
    first-match rule would quietly make them two people. With seventy-five real overlaps that
    case is a certainty, not a curiosity.
    """
    found = []
    email = (row.email or '').strip().lower()
    if email:
        found.append(('email', email))
    phone = normalise_phone(row.phone)
    if len(phone) >= 10:
        found.append(('phone', phone))
    return found


def _recency(row):
    """The more recently used profile wins a disagreement about name, theme or password."""
    return row.last_opened_at or row.created_at


class _Groups:
    """Rows joined into people, where sharing *any* handle joins them.

    Union-find, because the joining is transitive: a diary row with an email and a phone can
    pull together a money row that only has the email and another that only has the phone.
    """

    def __init__(self):
        self.parent = {}
        self.rows = {}          # key -> [(module, row)]

    def _find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def _union(self, a, b):
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self.parent[rb] = ra

    def add(self, module, row, keys):
        for key in keys:
            self.parent.setdefault(key, key)
            self.rows.setdefault(key, []).append((module, row))
        for other in keys[1:]:
            self._union(keys[0], other)

    def resolved(self):
        """`(representative key, {module: [rows]})` per person, rows deduplicated."""
        out = {}
        for key, entries in self.rows.items():
            root = self._find(key)
            bucket = out.setdefault(root, {})
            for module, row in entries:
                seen = bucket.setdefault(module, [])
                if not any(r.pk == row.pk for r in seen):
                    seen.append(row)
        return list(out.items())


class Command(BaseCommand):
    help = 'Build core Members from the per-module people, merging the ones who are the same.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually write. Without it nothing changes.')
        parser.add_argument('--dry-run', action='store_true',
                            help='The default. Prints the report and stops.')
        parser.add_argument('--only', choices=MODULES, default=None,
                            help='Just one module, for a staged run.')

    def handle(self, *args, **options):
        apply = options['apply'] and not options['dry_run']
        only = options['only']
        wanted = [only] if only else list(MODULES)

        loners = []             # rows with neither an email nor a phone
        counts = {}
        groups = _Groups()

        for module in wanted:
            rows = list(_rows(module))
            counts[module] = len(rows)
            for row in rows:
                keys = _keys(row)
                if not keys:
                    loners.append((module, row))
                    continue
                groups.add(module, row, keys)

        matched, singles, collisions = [], [], []
        for key, by_module in groups.resolved():
            doubled = {m: rows for m, rows in by_module.items() if len(rows) > 1}
            if doubled:
                collisions.append((key, by_module))      # two rows, one module, one handle
                continue
            flat = {m: rows[0] for m, rows in by_module.items()}
            (matched if len(flat) > 1 else singles).append((key, flat))

        self._report(counts, matched, singles, collisions, loners, wanted)
        if not apply:
            self.stdout.write('\nNothing was written. Re-run with --apply when the report reads '
                              'the way you expect.')
            return

        made, linked = 0, 0
        with transaction.atomic():
            for _key_, flat in matched + singles:
                member, fresh = self._member_for(flat)
                made += 1 if fresh else 0
                for row in flat.values():
                    if row.member_id != member.pk:
                        row.member = member
                        row.save(update_fields=['member'])
                        linked += 1
            for module, row in loners:
                member, fresh = self._member_for({module: row})
                made += 1 if fresh else 0
                if row.member_id != member.pk:
                    row.member = member
                    row.save(update_fields=['member'])
                    linked += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {made} member{"s" if made != 1 else ""} created, '
            f'{linked} profile{"s" if linked != 1 else ""} linked.'))
        if collisions:
            self.stdout.write(self.style.WARNING(
                f'{len(collisions)} collision(s) were left alone — see above.'))

    # ---- building one Member out of one or two profiles
    def _member_for(self, flat):
        existing = [row.member for row in flat.values() if row.member_id]
        if existing:
            return existing[0], False

        rows = sorted(flat.values(), key=_recency, reverse=True)
        first, rest = rows[0], rows[1:]

        email = next((r.email for r in rows if r.email), None)
        phone = next((normalise_phone(r.phone) for r in rows if r.phone), None)
        member = Member(
            name=first.name,
            email=(email or '').lower() or None,
            phone=phone or None,
            password_hash=first.password_hash or '',
            theme=getattr(first, 'theme', 'system'),
            syncup_token=next((r.syncup_token for r in rows if r.syncup_token), ''),
            is_active=any(r.is_active for r in rows),
            is_archived=all(r.is_archived for r in rows),
            created_at=min(r.created_at for r in rows),
            last_opened_at=max((r.last_opened_at for r in rows if r.last_opened_at),
                               default=None),
            open_count=sum(r.open_count for r in rows),
        )
        # The kindness that makes this safe to run on seventy-five people: when the two profiles
        # carry different passwords, keep the other one too. Either will sign them in, and the
        # first successful sign-in settles which and clears the spare.
        other = next((r.password_hash for r in rest
                      if r.password_hash and r.password_hash != member.password_hash), '')
        member.legacy_password_hash = other
        member.save()
        return member, True

    # ---- the report
    def _report(self, counts, matched, singles, collisions, loners, wanted):
        out = self.stdout
        out.write(self.style.MIGRATE_HEADING('\nWhat is there now'))
        for module in wanted:
            out.write(f'  {module:<10} {counts[module]} {"person" if counts[module] == 1 else "people"}')

        by_email = [m for m in matched if m[0][0] == 'email']
        by_phone = [m for m in matched if m[0][0] == 'phone']
        out.write(self.style.MIGRATE_HEADING('\nThe same person in both modules'))
        out.write(f'  matched on email   {len(by_email)}')
        out.write(f'  matched on phone   {len(by_phone)}')
        out.write(f'  → {len(matched)} member(s) instead of {len(matched) * 2} rows of identity')

        out.write(self.style.MIGRATE_HEADING('\nIn one module only'))
        for module in wanted:
            n = len([s for s in singles if module in s[1]])
            out.write(f'  {module:<10} {n}')
        if loners:
            out.write(f'  no email and no phone: {len(loners)} — each gets a member of its own, '
                      f'and can never be matched automatically')

        differing = [(k, f) for k, f in matched
                     if len({r.password_hash for r in f.values() if r.password_hash}) > 1]
        names = [(k, f) for k, f in matched
                 if len({r.name.strip().lower() for r in f.values()}) > 1]
        out.write(self.style.MIGRATE_HEADING('\nDisagreements, resolved automatically'))
        out.write(f'  two different passwords  {len(differing)}  → both kept; either one works '
                  f'until the next sign-in settles it')
        out.write(f'  two different names      {len(names)}  → the more recently used one wins')
        for key, flat in names:
            shown = ' / '.join(f'{m}: {r.name}' for m, r in flat.items())
            out.write(f'      {key[1]}  {shown}')

        out.write(self.style.MIGRATE_HEADING('\nNeeds a human — nothing will be done to these'))
        if not collisions:
            out.write('  none')
        for key, by_module in collisions:
            where = ', '.join(f'{m}×{len(rows)}' for m, rows in by_module.items())
            names_ = ', '.join(r.name for rows in by_module.values() for r in rows)
            out.write(f'  {key[0]} {key[1]}: {where} — {names_}')
        if collisions:
            out.write('  Two rows in one module share an email or a number. Usually a family '
                      'phone on two real people, occasionally one person signed up twice. '
                      'Sort them out by hand, then run again.')

        total = len(matched) + len(singles) + len(loners)
        out.write(self.style.MIGRATE_HEADING('\nResult'))
        out.write(f'  {total} member(s) would exist afterwards.')
