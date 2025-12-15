from datetime import datetime, date
from sqlalchemy import text

from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    flash,
    request,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)

from config import config
from extensions import db

login_manager = LoginManager()
login_manager.login_view = "login"


def create_app(config_name: str = "default") -> Flask:
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    db.init_app(app)
    login_manager.init_app(app)

    from models import User, Room, Booking, Hotel  # noqa: F401

    def ensure_schema_and_seed():
        """Создать таблицы и заполнить примерами, если база пустая."""
        db.create_all()
        # Однократное добавление столбца hotel_id для существующей таблицы rooms (SQLite)
        try:
            db.session.execute(text("ALTER TABLE rooms ADD COLUMN hotel_id INTEGER"))
            db.session.commit()
        except Exception:
            db.session.rollback()

        if not Hotel.query.first():
            hotel_a = Hotel(name="Отель Центр", city="Москва")
            hotel_b = Hotel(name="Городской", city="Санкт-Петербург")
            db.session.add_all([hotel_a, hotel_b])
            db.session.flush()

            sample_rooms = [
                Room(
                    number="101",
                    hotel_id=hotel_a.id,
                    room_type="Стандарт",
                    price_per_night=4500,
                    capacity=2,
                    description="Уютный номер в центре города.",
                ),
                Room(
                    number="202",
                    hotel_id=hotel_a.id,
                    room_type="Делюкс",
                    price_per_night=7200,
                    capacity=3,
                    description="Просторный номер с видом на город.",
                ),
                Room(
                    number="301",
                    hotel_id=hotel_b.id,
                    room_type="Стандарт",
                    price_per_night=3800,
                    capacity=2,
                    description="Спокойный номер рядом с набережной.",
                ),
            ]
            db.session.add_all(sample_rooms)
            db.session.commit()

    with app.app_context():
        ensure_schema_and_seed()

    @login_manager.user_loader
    def load_user(user_id: str):
        return User.query.get(int(user_id))

    @app.route("/")
    def index():
        from models import Hotel

        today = date.today()
        hotels = Hotel.query.order_by(Hotel.name).all()

        selected_hotel_id = request.args.get("hotel_id", type=int)

        rooms_query = Room.query.order_by(Room.price_per_night)
        if selected_hotel_id:
            rooms_query = rooms_query.filter(Room.hotel_id == selected_hotel_id)

        rooms = rooms_query.all()

        active_room_ids = {
            room_id
            for (room_id,) in Booking.query.with_entities(Booking.room_id)
            .filter(Booking.check_in <= today, Booking.check_out > today)
            .distinct()
        }
        available_count = len([room for room in rooms if room.id not in active_room_ids])

        return render_template(
            "index.html",
            rooms=rooms,
            hotels=hotels,
            selected_hotel_id=selected_hotel_id,
            total_rooms=len(rooms),
            available_count=available_count,
            active_room_ids=active_room_ids,
        )

    @app.route("/room/<int:room_id>")
    def room_detail(room_id: int):
        room = Room.query.get_or_404(room_id)
        hotel = room.hotel
        today = date.today()
        is_currently_booked = (
            Booking.query.filter(
                Booking.room_id == room.id,
                Booking.check_in <= today,
                Booking.check_out > today,
            ).first()
            is not None
        )
        return render_template(
            "room_detail.html",
            room=room,
            hotel=hotel,
            is_currently_booked=is_currently_booked,
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        from models import User  # local import to avoid circular

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            name = request.form.get("name", "").strip()
            password = request.form.get("password", "")

            if not email or not password or not name:
                flash("Заполните все поля", "danger")
            elif User.query.filter_by(email=email).first():
                flash("Пользователь с таким email уже существует", "danger")
            else:
                user = User(email=email, name=name)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                flash("Регистрация успешна, теперь можете войти", "success")
                return redirect(url_for("login"))

        return render_template("auth/register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        from models import User

        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")

            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                login_user(user)
                flash("Вы успешно вошли", "success")
                next_page = request.args.get("next") or url_for("index")
                return redirect(next_page)
            flash("Неверный email или пароль", "danger")

        return render_template("auth/login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Вы вышли из аккаунта", "info")
        return redirect(url_for("index"))

    @app.route("/book/<int:room_id>", methods=["GET", "POST"])
    @login_required
    def book_room(room_id: int):
        from models import Room, Booking

        room = Room.query.get_or_404(room_id)

        if request.method == "POST":
            check_in_str = request.form.get("check_in")
            check_out_str = request.form.get("check_out")

            try:
                check_in = datetime.strptime(check_in_str, "%Y-%m-%d").date()
                check_out = datetime.strptime(check_out_str, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                flash("Некорректные даты", "danger")
                return render_template("booking/book_room.html", room=room)

            if check_in >= check_out:
                flash("Дата выезда должна быть позже даты заезда", "danger")
                return render_template("booking/book_room.html", room=room)

            overlapping = Booking.query.filter(
                Booking.room_id == room.id,
                Booking.check_in < check_out,
                Booking.check_out > check_in,
            ).first()
            if overlapping:
                flash("На выбранные даты номер уже забронирован", "danger")
                return render_template("booking/book_room.html", room=room)

            nights = (check_out - check_in).days
            total_price = nights * room.price_per_night

            booking = Booking(
                user_id=current_user.id,
                room_id=room.id,
                check_in=check_in,
                check_out=check_out,
                total_price=total_price,
            )
            db.session.add(booking)
            db.session.commit()
            flash("Бронирование успешно создано", "success")
            return redirect(url_for("my_bookings"))

        return render_template("booking/book_room.html", room=room)

    @app.route("/my-bookings")
    @login_required
    def my_bookings():
        from models import Booking

        bookings = (
            Booking.query.filter_by(user_id=current_user.id)
            .order_by(Booking.check_in.desc())
            .all()
        )
        return render_template("booking/my_bookings.html", bookings=bookings)

    @app.post("/my-bookings/<int:booking_id>/delete")
    @login_required
    def delete_booking(booking_id: int):
        from models import Booking

        booking = Booking.query.filter_by(id=booking_id, user_id=current_user.id).first()
        if not booking:
            flash("Бронирование не найдено", "danger")
            return redirect(url_for("my_bookings"))

        db.session.delete(booking)
        db.session.commit()
        flash("Бронирование удалено", "success")
        return redirect(url_for("my_bookings"))

    @app.route("/admin/rooms")
    @login_required
    def admin_rooms():
        if not current_user.is_admin:
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))

        from models import Room

        rooms = Room.query.order_by(Room.number).all()
        return render_template("admin/rooms.html", rooms=rooms)

    @app.route("/admin/rooms/create", methods=["GET", "POST"])
    @login_required
    def admin_create_room():
        if not current_user.is_admin:
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))

        from models import Room, Hotel

        hotels = Hotel.query.order_by(Hotel.name).all()
        if request.method == "POST":
            number = request.form.get("number", "").strip()
            room_type = request.form.get("room_type", "").strip()
            price_per_night = float(request.form.get("price_per_night", "0") or "0")
            capacity = int(request.form.get("capacity", "1") or "1")
            description = request.form.get("description", "").strip()
            hotel_id = request.form.get("hotel_id", type=int)

            if not number or not room_type or price_per_night <= 0 or not hotel_id:
                flash("Заполните обязательные поля и выберите отель", "danger")
            else:
                room = Room(
                    number=number,
                    room_type=room_type,
                    hotel_id=hotel_id,
                    price_per_night=price_per_night,
                    capacity=capacity,
                    description=description,
                )
                db.session.add(room)
                db.session.commit()
                flash("Номер создан", "success")
                return redirect(url_for("admin_rooms"))

        return render_template("admin/room_form.html", hotels=hotels)

    @app.route("/admin/rooms/<int:room_id>/edit", methods=["GET", "POST"])
    @login_required
    def admin_edit_room(room_id: int):
        if not current_user.is_admin:
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))

        from models import Room, Hotel

        room = Room.query.get_or_404(room_id)
        hotels = Hotel.query.order_by(Hotel.name).all()

        if request.method == "POST":
            room.number = request.form.get("number", "").strip()
            room.room_type = request.form.get("room_type", "").strip()
            room.price_per_night = float(
                request.form.get("price_per_night", room.price_per_night) or "0"
            )
            room.capacity = int(request.form.get("capacity", room.capacity) or "1")
            room.description = request.form.get("description", "").strip()
            hotel_id = request.form.get("hotel_id", type=int)

            if not room.number or not room.room_type or room.price_per_night <= 0 or not hotel_id:
                flash("Заполните обязательные поля и выберите отель", "danger")
            else:
                room.hotel_id = hotel_id
                db.session.commit()
                flash("Номер обновлён", "success")
                return redirect(url_for("admin_rooms"))

        return render_template("admin/room_form.html", room=room, hotels=hotels)

    @app.route("/admin/bookings")
    @login_required
    def admin_bookings():
        if not current_user.is_admin:
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))

        from models import Booking

        bookings = Booking.query.order_by(Booking.check_in.desc()).all()
        return render_template("admin/bookings.html", bookings=bookings)

    @app.route("/admin/users")
    @login_required
    def admin_users():
        if not current_user.is_admin:
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))

        from models import User

        users = User.query.order_by(User.created_at.desc()).all()
        return render_template("admin/users.html", users=users)

    @app.route("/admin/hotels")
    @login_required
    def admin_hotels():
        if not current_user.is_admin:
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))

        from sqlalchemy.orm import joinedload
        from models import Hotel

        hotels = (
            Hotel.query.options(joinedload(Hotel.rooms))
            .order_by(Hotel.name)
            .all()
        )
        return render_template("admin/hotels.html", hotels=hotels)

    @app.route("/admin/hotels/create", methods=["GET", "POST"])
    @login_required
    def admin_create_hotel():
        if not current_user.is_admin:
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))

        from models import Hotel

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            city = request.form.get("city", "").strip()

            if not name:
                flash("Укажите название отеля", "danger")
            elif Hotel.query.filter_by(name=name).first():
                flash("Отель с таким названием уже есть", "danger")
            else:
                hotel = Hotel(name=name, city=city or None)
                db.session.add(hotel)
                db.session.commit()
                flash("Отель создан", "success")
                return redirect(url_for("admin_hotels"))

        return render_template("admin/hotel_form.html")

    @app.route("/admin/hotels/<int:hotel_id>/edit", methods=["GET", "POST"])
    @login_required
    def admin_edit_hotel(hotel_id: int):
        if not current_user.is_admin:
            flash("Недостаточно прав", "danger")
            return redirect(url_for("index"))

        from models import Hotel

        hotel = Hotel.query.get_or_404(hotel_id)

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            city = request.form.get("city", "").strip()

            duplicate = (
                Hotel.query.filter(Hotel.id != hotel.id, Hotel.name == name)
                .first()
                if name
                else None
            )
            if not name:
                flash("Укажите название отеля", "danger")
            elif duplicate:
                flash("Отель с таким названием уже есть", "danger")
            else:
                hotel.name = name
                hotel.city = city or None
                db.session.commit()
                flash("Отель обновлён", "success")
                return redirect(url_for("admin_hotels"))

        return render_template("admin/hotel_form.html", hotel=hotel)

    return app


if __name__ == "__main__":
    application = create_app()
    with application.app_context():
        from models import User, Room, Booking, Hotel  # noqa: F401

        db.create_all()
        # Однократное добавление столбца hotel_id для существующей таблицы rooms (SQLite)
        try:
            db.session.execute(text("ALTER TABLE rooms ADD COLUMN hotel_id INTEGER"))
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Примерные данные, если база пуста
        if not Hotel.query.first():
            hotel_a = Hotel(name="Отель Центр", city="Москва")
            hotel_b = Hotel(name="Городской", city="Санкт-Петербург")
            db.session.add_all([hotel_a, hotel_b])
            db.session.flush()

            sample_rooms = [
                Room(
                    number="101",
                    hotel_id=hotel_a.id,
                    room_type="Стандарт",
                    price_per_night=4500,
                    capacity=2,
                    description="Уютный номер в центре города.",
                ),
                Room(
                    number="202",
                    hotel_id=hotel_a.id,
                    room_type="Делюкс",
                    price_per_night=7200,
                    capacity=3,
                    description="Просторный номер с видом на город.",
                ),
                Room(
                    number="301",
                    hotel_id=hotel_b.id,
                    room_type="Стандарт",
                    price_per_night=3800,
                    capacity=2,
                    description="Спокойный номер рядом с набережной.",
                ),
            ]
            db.session.add_all(sample_rooms)
            db.session.commit()
    application.run()


