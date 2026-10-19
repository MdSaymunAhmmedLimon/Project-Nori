"""Small RFC 5545 calendar downloads, generated without filesystem storage."""

from datetime import timedelta, timezone as datetime_timezone

from django.utils import timezone


def calendar_text(value):
    return (str(value).replace("\\", "\\\\").replace("\r\n", "\n")
            .replace("\r", "\n").replace("\n", "\\n")
            .replace(";", "\\;").replace(",", "\\,"))


def fold_line(value):
    """Fold at 75 UTF-8 octets without splitting a multibyte character."""
    lines, current, length = [], "", 0
    for character in value:
        size = len(character.encode("utf-8"))
        if length + size > 75:
            lines.append(current)
            current, length = " ", 1
        current += character
        length += size
    lines.append(current)
    return "\r\n".join(lines)


def reservation_calendar(reservation):
    def timestamp(value):
        return value.astimezone(datetime_timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    quote = reservation.quote
    description = (
        f"Pickup: {quote.pickup_label}\nDestination: {quote.destination_label}\n"
        f"Estimated fare: {quote.currency} {quote.fare}\n"
        "Driver availability is not guaranteed. Check Nori before pickup.\n"
        "This reminder does not update automatically. If you change or cancel "
        "your reservation, update or remove the calendar event."
    )
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Nori//Ride reservations//EN",
        "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
        f"UID:{reservation.pk}@nori-reservations",
        f"DTSTAMP:{timestamp(timezone.now())}",
        f"DTSTART:{timestamp(reservation.scheduled_for)}",
        f"DTEND:{timestamp(reservation.scheduled_for + timedelta(minutes=max(1, quote.estimated_minutes)))}",
        f"SUMMARY:{calendar_text('Nori ride to ' + quote.destination_label)}",
        f"LOCATION:{calendar_text(quote.pickup_label)}",
        f"DESCRIPTION:{calendar_text(description)}",
        "STATUS:TENTATIVE", "CLASS:PRIVATE", "TRANSP:OPAQUE",
        "BEGIN:VALARM", "TRIGGER:-PT15M", "ACTION:DISPLAY",
        "DESCRIPTION:Your Nori pickup is in 15 minutes", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"
