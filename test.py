import mmap
import time

print("--- DIAGNOSTIC LMU ---")
print("Lance le jeu et mets-toi AU VOLANT (en piste).")
print("Appuie sur CTRL+C pour arrêter.")

# Les différents noms possibles que le jeu peut utiliser
NOMS_POSSIBLES = [
    "$rFactor2SMMP_Scoring$",      # Standard
    "$rFactor2SMMP_Telemetry$",    # Standard Télémétrie
    "rFactor2SMMP_Scoring",        # Sans le $
    "Local\\$rFactor2SMMP_Scoring$" # Avec préfixe Windows
]

while True:
    found_something = False
    print("\nTentative de connexion...")
    
    for nom in NOMS_POSSIBLES:
        try:
            # On essaie d'ouvrir juste 1 octet pour voir si la porte s'ouvre
            # On ne se soucie pas de la structure pour l'instant
            shm = mmap.mmap(0, 10, tagname=nom, access=mmap.ACCESS_READ)
            print(f"✅ SUCCÈS ! J'ai trouvé : {nom}")
            shm.close()
            found_something = True
        except FileNotFoundError:
            print(f"❌ Pas trouvé : {nom}")
        except Exception as e:
            print(f"⚠️ Erreur bizarre sur {nom} : {e}")

    if found_something:
        print("\n🎉 VICTOIRE : Le lien est possible !")
        print("Cela veut dire que mon fichier précédent 'rF2.py' avait une structure trop stricte.")
        break
    else:
        print("🔴 ECHEC : Aucune mémoire trouvée.")
        print("Vérifications à faire :")
        print("1. As-tu copié la DLL dans 'Le Mans Ultimate/Plugins' OU 'Le Mans Ultimate/Bin64/Plugins' ?")
        print("2. Le plugin est-il bien sur 'ON' dans le jeu ?")
    
    time.sleep(3)