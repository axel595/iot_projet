<?php
// save_data.php

$host = "localhost";
$dbname = "parking_iot";
$user = "root";
$password = "";

try {
    $pdo = new PDO(
        "mysql:host=$host;dbname=$dbname;charset=utf8mb4",
        $user,
        $password,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]
    );
} catch (PDOException $e) {
    http_response_code(500);
    exit("Erreur DB : " . $e->getMessage());
}

// Récupération du JSON envoyé par le client
$data = json_decode(file_get_contents("php://input"), true);

if (!isset($data["places_disponibles"], $data["timestamp"])) {
    http_response_code(400);
    exit("Données invalides");
}

// Préparer et exécuter l'insertion
$stmt = $pdo->prepare("
    INSERT INTO historique_places (places_disponibles, timestamp_mqtt)
    VALUES (?, ?)
");

$stmt->execute([
    $data["places_disponibles"],
    $data["timestamp"]
]);

echo "OK";
