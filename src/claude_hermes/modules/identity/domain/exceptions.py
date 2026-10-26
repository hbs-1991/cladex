"""Identity's own errors.

`LoginError` stays a `ValueError` subclass this wave: the auth routes catch it in
`except (LoginError, ValueError, json.JSONDecodeError)` tuples that must keep
behaving identically, and a service-style `ApplicationException` would change
which branch a malformed payload takes.

The three added in Wave 4a are what a `users` INSERT can mean, and `OwnerImmutable`
is what the owner's own user list can refuse. They are plain exceptions, not
`ApplicationException`: a channel turns each one into its own answer — a verdict
string on the chat path, a status code on the users router — and the domain stays
ignorant of both vocabularies.
"""
from __future__ import annotations


class LoginError(ValueError):
    pass


class UserExists(Exception):
    """A row already carries this id.

    The insert used `ON CONFLICT (id) DO NOTHING`, so this is "somebody else got
    there first", never a corrupted write. In the allowlist path that is benign
    (two processes admitted the same person at once); in the claim path it means
    the deploy is not this caller's to take.
    """


class OwnerExists(Exception):
    """The `users_one_owner` partial index refused a second owner.

    This IS the claim race's answer. Two first private chats arriving together
    both run the INSERT; PostgreSQL picks one, the loser lands here, and
    `AccessControl.check` rolls back and says `"deny"`. There is no
    check-then-insert anywhere, because a check-then-insert is a race.
    """


class UnknownUser(LookupError):
    """Asked where somebody's workspace is, and nobody by that id has a row.

    A `LookupError`, so a caller that may not import this module (learning's
    skills providers, through the workspace resolver) can still tell "no such
    user" from a bug and answer 401 rather than 500.

    Raised rather than answered with the owner's directory: guessing here would
    silently run a stranger's turn in the owner's tree, which is exactly the
    failure Wave 4a exists to make impossible.
    """


class OwnerImmutable(Exception):
    """Asked to take the owner's own access away, or to change the owner's grants.

    Refused wherever it is asked, because `is_owner` gates every mutation in the
    application: an owner who disabled themselves could not undo it from Telegram,
    from the browser or from a tool, and the deployment would need SQL on the host
    to get its administrator back. The owner's shell and learning are not grants
    either — they come with owning the lane (`Grants.of`), so a switch for them
    would be a control that changes nothing.
    """
