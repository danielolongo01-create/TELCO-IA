# Base de connaissances NOC (fiches de référence)

Les repères chiffrés ci-dessous sont des valeurs indicatives d'ingénierie, pas des seuils normatifs 3GPP, sauf mention contraire. À adapter aux seuils de l'opérateur.

## RSRP : interprétation
Le RSRP (Reference Signal Received Power) est la puissance moyenne des signaux de référence d'une cellule, en dBm. Sa définition figure dans 3GPP TS 36.214 (LTE) et TS 38.215 (NR). Repères indicatifs : supérieur à -80 dBm excellent ; -80 à -90 bon ; -90 à -100 moyen ; -100 à -110 faible, débits réduits en bordure de cellule ; inférieur à -110 dBm très faible, risque d'échecs d'accès et de décrochages. Le seuil d'alerte de ce projet est -110 dBm.

## SINR : interprétation
Le SINR est le rapport signal sur interférence plus bruit, en dB. Repères indicatifs : supérieur à 20 dB excellent ; 13 à 20 bon ; 0 à 13 moyen à faible ; inférieur à 0 dB dégradé (le signal est plus faible que bruit et interférences), avec MCS bas et nombreuses retransmissions. Le seuil d'alerte de ce projet est 0 dB. Sur Android, la valeur LTE provient du champ rssnr.

## RSRQ : interprétation
Le RSRQ vaut N x RSRP / RSSI. Il reflète à la fois la qualité du signal et la charge/l'interférence sur la bande. Repères indicatifs : supérieur à -10 dB bon ; -10 à -15 moyen ; inférieur à -15 dB faible. Un RSRQ bas avec un RSRP correct oriente vers de l'interférence ou une cellule très chargée.

## BLER : définition et seuils
Le BLER (Block Error Rate) est la proportion de blocs de transport reçus en erreur. L'adaptation de lien (AMC) vise en général un BLER d'environ 10 % sur la première transmission ; le HARQ corrige ensuite les erreurs. Un BLER durablement supérieur à 10-15 % indique un décalage entre le CQI rapporté et le canal réel, de l'interférence, de la mobilité rapide ou une mauvaise couverture. Le seuil d'alerte de ce projet est 15 %. Attention : Android n'expose pas le BLER sans accès aux équipements radio ; dans ce projet, le BLER est estimé à partir du SINR et de la perte de paquets, et doit être présenté comme une estimation.

## CQI, MCS et débit
Le terminal rapporte un CQI (0 à 15) ; le réseau en déduit le schéma de modulation et de codage (MCS). Un CQI bas conduit à un MCS bas et donc à un débit réduit. Références : 3GPP TS 36.213 (LTE) et TS 38.214 (NR). Un CQI qui chute alors que le RSRP reste stable pointe vers l'interférence plutôt que vers la couverture.

## Charge PRB et congestion
Le taux d'utilisation des PRB indique la charge radio de la cellule. Repères indicatifs : au-dessus de 70-80 % de façon prolongée, la latence et la perte de paquets augmentent même si le signal est bon. Dans ce cas, le problème est la capacité, pas la couverture.

## Diagnostic : couverture
RSRP faible et SINR faible ensemble : problème de couverture (distance, obstacle, antenne mal orientée ou défaillante). Actions : vérifier tilt et azimut, puissance d'émission, état de l'antenne et des feeders (VSWR), envisager un site ou une small cell supplémentaire.

## Diagnostic : interférence
RSRP correct mais SINR faible, avec RSRQ et CQI bas : interférence. Causes fréquentes : sur-portée (overshooting) d'un site voisin, conflits de PCI (même PCI mod 3 en LTE), relations de voisinage mal configurées, source externe. Actions : ajuster le tilt, revoir le plan de PCI, vérifier les voisines, appliquer des techniques de coordination d'interférence (ICIC/eICIC).

## Diagnostic : congestion
RSRP et SINR corrects mais débit faible, latence élevée et charge PRB haute : congestion. Actions : répartition de charge entre cellules ou porteuses, ajout d'une porteuse, délestage, politiques de QoS.

## Diagnostic : transport, backhaul et coeur
Radio correcte (RSRP, SINR bons) mais perte de paquets et latence élevées : suspecter le transport (liaison hertzienne ou fibre saturée, erreurs de liaison) ou le coeur de réseau. Vérifier les compteurs d'interface du site, la capacité du backhaul et les alarmes de transmission.

## Alimentation des sites (heuristique d'exploitation)
Dans les réseaux urbains où l'alimentation électrique est irrégulière, vérifier en premier l'alimentation du site (secteur, groupe électrogène, batteries) avant d'investiguer la radio, car une coupure partielle produit des symptômes qui ressemblent à une dégradation radio. C'est une heuristique, à confirmer par les alarmes du site.

## Limites des mesures de la sonde Android
La sonde mesure la cellule serveuse vue par le téléphone : RSRP et SINR sont réels, mais le CELL_ID envoyé est une étiquette configurée, pas l'identité radio réelle de la cellule. Le ping vers 8.8.8.8 mesure le chemin complet (radio, transport, Internet), pas seulement la radio ; une perte de paquets peut donc venir d'ailleurs que de la cellule. Si le téléphone se déplace, il change de cellule sans que l'étiquette change. Le BLER est estimé.

## Bonnes pratiques de réponse du NOC
Toujours distinguer mesure réelle et valeur estimée. Citer les valeurs et leur ancienneté. Proposer d'abord des vérifications peu coûteuses (alarmes, alimentation, configuration récente) avant des actions lourdes. Indiquer le niveau de confiance du diagnostic.
