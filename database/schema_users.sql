USE bovibot;

CREATE TABLE IF NOT EXISTS utilisateurs (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    nom          VARCHAR(100),
    prenom       VARCHAR(100),
    username     VARCHAR(100) NOT NULL UNIQUE,
    email        VARCHAR(150) NULL,
    mot_de_passe VARCHAR(255) NOT NULL,
    nom_elevage  VARCHAR(200),
    telephone    VARCHAR(20),
    localite     VARCHAR(100),
    role         ENUM('admin','eleveur') DEFAULT 'eleveur',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tokens (
    token      VARCHAR(64) PRIMARY KEY,
    user_id    INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES utilisateurs(id) ON DELETE CASCADE
);

ALTER TABLE animaux
    ADD COLUMN IF NOT EXISTS user_id INT NULL,
    ADD FOREIGN KEY IF NOT EXISTS (user_id) REFERENCES utilisateurs(id);