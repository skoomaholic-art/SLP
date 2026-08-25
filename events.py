from models import SportEvent


EVENTS = [
    SportEvent(
        date="25 августа",
        broadcast_start="18:55",
        sport="Хоккей",
        tournament="Кубок Республики Казахстан. Финал",
        title="Торпедо – Сарыарқа",
        channel="Qazsport",
        is_live=True,
        event_start="19:00",
        verification_status="confirmed",
        time_difference_minutes=5,
    ),

    SportEvent(
        date="25 августа",
        broadcast_start="21:00",
        sport="Теннис",
        tournament="US Open",
        title="Квалификация",
        channel="Eurosport 1",
        is_live=False,
        event_start="21:00",
        verification_status="confirmed",
        time_difference_minutes=0,
    ),

    SportEvent(
        date="25 августа",
        broadcast_start="23:00",
        sport="Футбол",
        tournament="Ла Лига",
        title="Валенсия – Бетис",
        channel="Setanta Sports 1",
        is_live=True,
        event_start="23:05",
        verification_status="confirmed",
        time_difference_minutes=5,
    ),
]