from datetime import datetime, timedelta


def assign_dates(base_date, times):
    current_date = datetime.strptime(
        base_date,
        "%Y-%m-%d"
    ).date()

    previous_minutes = None
    result = []

    for time_text in times:
        hour, minute = map(
            int,
            time_text.split(":")
        )

        current_minutes = hour * 60 + minute

        if (
            previous_minutes is not None
            and current_minutes < previous_minutes
        ):
            current_date += timedelta(days=1)

        result.append(
            f"{current_date.isoformat()} {time_text}"
        )

        previous_minutes = current_minutes

    return result