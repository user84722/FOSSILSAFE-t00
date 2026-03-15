import random

DINOSAURS = [
  "Tyrannosaurus", "Velociraptor", "Triceratops", "Stegosaurus", "Brachiosaurus",
  "Spinosaurus", "Allosaurus", "Ankylosaurus", "Diplodocus", "Pteranodon",
  "Parasaurolophus", "Carnotaurus", "Iguanodon", "Apatosaurus", "Baryonyx",
  "Pachycephalosaurus", "Therizinosaurus", "Giganotosaurus", "Compsognathus",
  "Gallimimus", "Dilophosaurus", "Mosasaurus", "Plesiosaurus", "Dimetrodon",
  "Archaeopteryx", "Troodon", "Argentinosaurus", "Microraptor", "Deinonychus",
  "Plateosaurus", "Coelophysis", "Ceratosaurus", "Oviraptor", "Maiasaura",
  "Styracosaurus", "Amargasaurus", "Saltasaurus", "Kentrosaurus", "Suchomimus",
  "Corythosaurus", "Edmontosaurus", "Pachyrhinosaurus", "Ouranosaurus",
  "Herrerasaurus", "Dreadnoughtus", "Patagotitan", "Supersaurus", "Titanosaurus",
  "Megalosaurus", "Carcharodontosaurus"
]

ADJECTIVES = [
  "Mighty", "Swift", "Ancient", "Roaring", "Thunderous", "Fossilized",
  "Prehistoric", "Giant", "Fierce", "Noble", "Armored", "Crested",
  "Golden", "Silver", "Emerald", "Obsidian", "Amber", "Ruby",
  "Eternal", "Primeval", "Jurassic", "Cretaceous", "Triassic",
  "Lost", "Found", "Hidden", "Silent", "Fast", "Heavy", "Strong"
]

def get_random_name():
    """Generate a random cool dinosaur nickname."""
    dino = random.choice(DINOSAURS)
    adj = random.choice(ADJECTIVES)
    return f"{adj} {dino}"
