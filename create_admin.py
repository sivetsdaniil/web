"""Скрипт для создания администратора"""
from app import create_app, db
from models import User

app = create_app()

with app.app_context():
    # Проверяем, есть ли уже админ
    admin = User.query.filter_by(email="admin@example.com").first()
    
    if admin:
        print("Администратор уже существует!")
        print(f"Email: {admin.email}")
        print(f"Имя: {admin.name}")
    else:
        # Создаём нового админа
        admin = User(email="admin@example.com", name="Admin")
        admin.set_password("admin123")
        admin.is_admin = True
        db.session.add(admin)
        db.session.commit()
        print("✅ Администратор успешно создан!")
        print("Email: admin@example.com")
        print("Пароль: admin123")

