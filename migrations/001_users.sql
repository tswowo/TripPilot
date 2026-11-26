CREATE TABLE IF NOT EXISTS users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username varchar(50) NOT NULL UNIQUE,
    password_hash text NOT NULL,
    create_time timestamptz NOT NULL DEFAULT now()
    update_time timestamptz NOT NULL DEFAULT now()
);
