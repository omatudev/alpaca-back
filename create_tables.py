import os
import psycopg2
from urllib.parse import urlparse

# Obtener la URL de la base de datos desde las variables de entorno
database_url = os.getenv("DATABASE_URL")

if not database_url:
    print("Error: La variable DATABASE_URL no está configurada.")
    exit(1)

# Conectar a la base de datos
try:
    result = urlparse(database_url)
    conn = psycopg2.connect(
        dbname=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port,
        sslmode="require"
    )
    cursor = conn.cursor()

    # Crear tabla de ejemplo
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        comment TEXT
    );
    """)

    # Confirmar cambios
    conn.commit()
    print("Tablas creadas exitosamente.")

except Exception as e:
    print(f"Error al conectar o crear tablas: {e}")

finally:
    if 'cursor' in locals():
        cursor.close()
    if 'conn' in locals():
        conn.close()