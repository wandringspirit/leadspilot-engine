"""Email outreach: send the businesses a job found a short sequence of emails.

A *campaign* takes the businesses of one or more jobs (one lead per business,
with an address of its own - see ``leads.py``) and emails each of them a first
email and follow-ups on the days you choose, from your own mailbox. Any reply,
bounce or unsubscribe stops the follow-ups for that business.

* ``models.py``    Campaign, Lead, Step and the request bodies
* ``templates.py`` placeholders, optional parts, the default emails (pure)
* ``leads.py``     which businesses of a job become leads, and why the others don't (pure)
* ``schedule.py``  the send window: days, hours, time zone (pure)
* ``mailbox.py``   SMTP to send, IMAP to notice replies; message building and parsing
* ``manager.py``   OutreachManager: persistence, the API's operations, the send loop

See docs/OUTREACH.md.
"""
