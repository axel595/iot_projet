<?php
$mysqli = new mysqli("localhost", "root", "", "parking_iot");
if ($mysqli->connect_errno) { exit("Échec de la connexion à MySQL : " . $mysqli->connect_error); }

function fetchData($mysqli, $query) {
    $result = $mysqli->query($query);
    $data = [];
    while ($row = $result->fetch_assoc()) { $data[] = $row; }
    return $data;
}

$panel1 = fetchData($mysqli, "SELECT UNIX_TIMESTAMP(date_reception) AS time, places_disponibles AS value FROM historique_places ORDER BY date_reception ASC");
$panel2 = fetchData($mysqli, "SELECT DATE(date_reception) AS day, AVG(places_disponibles) AS value FROM historique_places GROUP BY DATE(date_reception) ORDER BY day ASC");
$panel3 = fetchData($mysqli, "SELECT DATE(date_reception) AS day, SUM(CASE WHEN places_disponibles = 0 THEN 1 ELSE 0 END) AS complet_count FROM historique_places GROUP BY DATE(date_reception) ORDER BY day ASC");
$panel4 = fetchData($mysqli, "SELECT MAX(places_disponibles) AS max_places, AVG(places_disponibles) AS avg_places FROM historique_places");
$fill_percent = $panel4 ? round(100 * ($panel4[0]['max_places'] - $panel4[0]['avg_places']) / $panel4[0]['max_places']) : 0;
?>

<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Parking – Dashboard Noir & Jaune</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<!-- MQTT.js -->
<script src="https://unpkg.com/mqtt/dist/mqtt.min.js"></script>
<!-- Chart.js -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<!-- Leaflet -->
<link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>

<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

/* --- FOND ET TYPO --- */
body { margin:0; font-family:'Inter',sans-serif; background:#121212; color:#fff; }
header { background:#1a1a1a; color:#FFD700; padding:20px; text-align:center; font-weight:700; font-size:1.8rem; }

/* --- GRID RESPONSIVE --- */
.grid-container { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:20px; padding:20px; }

/* --- CARD --- */
.card { background:#1e1e1e; border-radius:20px; padding:20px; box-shadow:0 8px 20px rgba(0,0,0,0.6); transition:0.3s; cursor:pointer; display:flex; flex-direction:column; }
.card:hover { transform:translateY(-5px); box-shadow:0 12px 30px rgba(0,0,0,0.8); }
.card.expanded { position:fixed; top:50%; left:50%; width:90vw; height:90vh; transform:translate(-50%,-50%); z-index:999; overflow:auto; }

/* --- TEXTES ET ELEMENTS --- */
.card h2 { margin:0 0 10px 0; color:#FFD700; font-size:1.3rem; }
#places { font-size:4rem; font-weight:700; margin:15px 0; }
.vert { color:#10b981; } .orange { color:#f59e0b; } .rouge { color:#ef4444; }
#etat { font-size:1.3rem; font-weight:600; color:#FFD700; }
.info, .mqtt { color:#ccc; font-size:0.9rem; margin-top:5px; }

canvas { width:100%; height:300px; }
.gauge { font-size:3rem; font-weight:700; text-align:center; margin-top:30px; color:#FFD700; }

#map { width:100%; height:300px; border-radius:15px; }
</style>
</head>
<body>

<header>Parking CESI</header>

<div class="grid-container">
    <div class="card" id="places-card">
        <h2>Places disponibles</h2>
        <div id="etat">--</div>
        <div id="places">--</div>
        <div class="info" id="update">Dernière mise à jour : --</div>
        <div class="mqtt" id="mqttStatus">Connexion MQTT...</div>
    </div>

    <div class="card" id="map-card">
        <h2>Localisation du parking</h2>
        <div id="map"></div>
    </div>

    <div class="card" id="chartEvolution-card">
        <h2>Évolution des places</h2>
        <canvas id="chartEvolution"></canvas>
    </div>

    <div class="card" id="chartMoyenne-card">
        <h2>Moyenne des places par jour</h2>
        <canvas id="chartMoyenne"></canvas>
    </div>

    <div class="card" id="chartComplet-card">
        <h2>Nombre de fois où le parking était complet</h2>
        <canvas id="chartComplet"></canvas>
    </div>

    <div class="card" id="fillPercent-card">
        <h2>Pourcentage de remplissage</h2>
        <div class="gauge" id="fillPercent"><?= $fill_percent ?>%</div>
    </div>
</div>

<script>
// ---------- CARD CLICK ----------
document.querySelectorAll('.card').forEach(card=>{
    card.addEventListener('click',()=>{ card.classList.toggle('expanded'); });
});

// ---------- MQTT ----------
const client = mqtt.connect("ws://10.54.128.186:9001");
const topic = "parking/places";
const placesEl=document.getElementById("places");
const etatEl=document.getElementById("etat");
const updateEl=document.getElementById("update");
const mqttEl=document.getElementById("mqttStatus");
const MAX_POINTS=50;

let chartEvolution=null;

// ---------- Chart.js ----------
const panel1=<?= json_encode($panel1) ?>;
const panel2=<?= json_encode($panel2) ?>;
const panel3=<?= json_encode($panel3) ?>;

chartEvolution=new Chart(document.getElementById("chartEvolution").getContext("2d"),{
    type:'line',
    data:{ labels:panel1.map(d=>new Date(d.time*1000).toLocaleTimeString()), datasets:[{label:'Places', data:panel1.map(d=>d.value), borderColor:'#FFD700', backgroundColor:'rgba(255,215,0,0.3)', fill:true, tension:0.3}] },
    options:{ responsive:true, animation:{duration:300}, plugins:{legend:{display:false}} }
});

new Chart(document.getElementById("chartMoyenne").getContext("2d"),{
    type:'line', data:{ labels:panel2.map(d=>d.day), datasets:[{label:'Moyenne', data:panel2.map(d=>d.value), borderColor:'#FFD700', backgroundColor:'rgba(255,215,0,0.3)', fill:true, tension:0.3}] },
    options:{ responsive:true, plugins:{legend:{display:false}} }
});

new Chart(document.getElementById("chartComplet").getContext("2d"),{
    type:'bar', data:{ labels:panel3.map(d=>d.day), datasets:[{label:'Complet', data:panel3.map(d=>d.complet_count), backgroundColor:'#FFD700'}] },
    options:{ responsive:true, plugins:{legend:{display:false}} }
});

// ---------- MQTT Realtime ----------
client.on("connect",()=>{ mqttEl.innerText="🟢 MQTT connecté"; client.subscribe(topic); });
client.on("message",(topic,message)=>{
    try{
        const data=JSON.parse(message.toString());
        const places=data.places_disponibles;
        const timestamp=data.timestamp;
        afficherPlaces(places,timestamp);
        updateChartsRealtime(places,timestamp);
        fetch("save_data.php",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({places_disponibles:places,timestamp:timestamp})});
    }catch(e){console.error(e);}
});
client.on("close",()=>{ mqttEl.innerText="🔴 MQTT déconnecté"; });

function afficherPlaces(places,timestamp){
    placesEl.className=""; etatEl.className="";
    if(places===0){ placesEl.innerText="COMPLET"; placesEl.classList.add("rouge"); etatEl.innerText="Parking complet"; etatEl.classList.add("rouge"); }
    else if(places<=5){ placesEl.innerText=places; placesEl.classList.add("orange"); etatEl.innerText="Presque plein"; etatEl.classList.add("orange"); }
    else{ placesEl.innerText=places; placesEl.classList.add("vert"); etatEl.innerText="Parking ouvert"; etatEl.classList.add("vert"); }
    if(timestamp){ const date=new Date(timestamp*1000); updateEl.innerText="Dernière mise à jour : "+date.toLocaleString(); }
}

function updateChartsRealtime(places,timestamp){
    const timeLabel=new Date(timestamp*1000).toLocaleTimeString();
    chartEvolution.data.labels.push(timeLabel);
    chartEvolution.data.datasets[0].data.push(places);
    if(chartEvolution.data.labels.length>MAX_POINTS){ chartEvolution.data.labels.shift(); chartEvolution.data.datasets[0].data.shift(); }
    chartEvolution.update();
}

// ---------- Map Leaflet Satellite ----------
const lat=48.65502929012028;
const lon=6.132980087811542;
const address="Campus CESI bat.Orion, 19 Av. de la Forêt de Haye Bâtiment Orion, 54500 Vandœuvre-lès-Nancy";

const map=L.map('map',{zoomControl:true}).setView([lat,lon],17);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{
    attribution:'Tiles &copy; Esri', maxZoom:20
}).addTo(map);
L.marker([lat,lon]).addTo(map).bindPopup(address).openPopup();
</script>

</body>
</html>