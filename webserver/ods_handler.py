"""
Lecture et génération de fichiers ODS pour le workflow CSV de l'algo.

parse_ods(data)          -> dict de feuilles : {nom_feuille: list[dict]}
generate_ods_modele(...) -> bytes du fichier ODS modèle (4 feuilles)
"""
from __future__ import annotations

import io

from odf.opendocument import OpenDocumentSpreadsheet, load as odf_load
from odf.style import Style, TableCellProperties, TextProperties
from odf.table import (
    Table, TableCell, TableRow,
    ContentValidation, ContentValidations,
)
from odf.text import P

# Disciplines standard (correspond à preps_modele.csv)
_DEFAULT_PREPS: list[tuple[str, str, int, int]] = [
    ("Lettres", "Lettres", 30, 20),
    ("Arts", "Arts", 30, 30),
    ("Histoire-Géographie, Géopolitique et Sciences Politiques", "HGGSP", 20, 20),
    ("Humanités, Littérature et Philosophie", "HLP", 20, 20),
    ("Numérique Sciences Informatiques", "NSI", 20, 20),
    ("Physique-Chimie", "PC", 20, 20),
    ("Sciences de la Vie et de la Terre", "SVT", 20, 20),
    ("Sciences Économiques et Sociales", "SES", 30, 20),
    ("Anglais", "Anglais", 20, 20),
    ("Mathématiques", "Maths", 20, 20),
    ("Management", "Mana", 40, 20),
    ("Droit Economie", "Droit Eco", 20, 20),
    ("Langues, Littératures et Cultures Etrangères", "LLCE", 20, 20),
    ("Anglais Monde comtemporain", "AMC", 20, 20),
    ("Philosophie", "Philo", 20, 20),
    ("Sciences Industrielles de L'ingénieur", "SII", 20, 20),
]

# 249 lycées académie Aix-Marseille (source : data.gouv.fr, annuaire éducation)
# Format : (UAI, Nom, Ville, Téléphone)
_LYCEES_AIM: list[tuple[str, str, str, str]] = [
    ('0134010N', "ACADEMIE D'ART DENTAIRE (LP PR HC)", 'Aix-en-Provence', '0442397670'),
    ('0134590U', "ECOLE PRIVE PIGIER LE COURS MESSIDORO SITE D'AIX EN PROVENCE", 'Aix-en-Provence', '0495051880'),
    ('0131669U', 'Ecole privée Val Saint André (Ecole secondaire générale)', 'Aix-en-Provence', '0442271447'),
    ('0132309P', 'Ecole technique privée Susini Esthétique (LP PR HC)', 'Aix-en-Provence', '0442204220'),
    ('0133148B', 'IBS Of Provence (Ecole secondaire générale privée)', 'Aix-en-Provence', '0442240340'),
    ('0131685L', 'L.T.P. CLOVIS HUGUES', 'Aix-en-Provence', '0442385196'),
    ('0133305X', 'Lycée Clovis Hugues', 'Aix-en-Provence', '0442381511'),
    ('0131319N', 'Lycée La Nativité', 'Aix-en-Provence', '0442934570'),
    ('0130002G', 'Lycée Paul Cézanne', 'Aix-en-Provence', '0442171400'),
    ('0131320P', 'Lycée Sainte-Catherine de Sienne', 'Aix-en-Provence', '0442234898'),
    ('0131862D', 'Lycée du Sacré-Coeur', 'Aix-en-Provence', '0442384132'),
    ('0133525L', 'Lycée international Georges Duby', 'Aix-en-Provence', '0442608600'),
    ('0131596P', "Lycée militaire d'Aix-en-Provence", 'Aix-en-Provence', '0442238958'),
    ('0131391S', 'Lycée polyvalent Saint-Éloi', 'Aix-en-Provence', '0442234499'),
    ('0133395V', 'Lycée polyvalent Sainte-Marie', 'Aix-en-Provence', '0442231112'),
    ('0130003H', 'Lycée polyvalent Vauvenargues', 'Aix-en-Provence', '0442174040'),
    ('0133425C', 'Lycée polyvalent privé Célony', 'Aix-en-Provence', '0442235965'),
    ('0130001F', 'Lycée polyvalent Émile Zola', 'Aix-en-Provence', '0442938700'),
    ('0130006L', 'Lycée professionnel Gambetta', 'Aix-en-Provence', '0442932828'),
    ('0130170P', "Section d'Enseignement Professionnel du LPO Vauvenargues", 'Aix-en-Provence', '0442174040'),
    ('0132569X', "Section d'enseignement professionnel du Lycée polyvalent Emile Zola", 'Aix-en-Provence', '0442938700'),
    ('0134253C', 'Lycée régional Monte-Cristo', 'Allauch', '0491641923'),
    ('0840001V', 'Lycée polyvalent Charles de Gaulle', 'Apt', '0490741119'),
    ('0840952D', "Section d'enseignement professionnel du Lycée", 'Apt', '0490741119'),
    ('0134360U', "LEAP Domaine du possible d'Arles", 'Arles', ''),
    ('0130011S', 'Lycée Louis Pasquet', 'Arles', '0490183515'),
    ('0130010R', 'Lycée Polyvalent Montmajour', 'Arles', '0490968050'),
    ('0133274N', "Lycée polyvalent Jeanne d'Arc - Campus Vincentien d'Arles", 'Arles', '0490520842'),
    ('0130171R', 'Lycée professionnel Charles Privat', 'Arles', '0490496044'),
    ('0132300E', "SGT Lycée technologique privé Jeanne d'Arc", 'Arles', '0490520840'),
    ('0130012T', "Section d'enseignement professionnel du lycée polyvalent Montmajour (Perdiguier)", 'Arles', '0490968050'),
    ('0132810J', 'Lycée Sainte-Marie', 'Aubagne', '0442031540'),
    ('0130013U', 'Lycée professionnel Gustave Eiffel', 'Aubagne', '0442031444'),
    ('0131549N', 'Lycée régional Irène et Frédéric Joliot-Curie', 'Aubagne', '0442185151'),
    ('0840083J', 'Ecole hôtelière privée (LT PR HC)', 'Avignon', '0490138633'),
    ('0840077C', 'GROUPE BELMONT (LP PR HC)', 'Avignon', '0490821500'),
    ('0840003X', 'Lycée Frédéric Mistral', 'Avignon', '0490804500'),
    ('0840059H', 'Lycée Louis Pasteur', 'Avignon', '0490145777'),
    ('0840072X', 'Lycée Saint-Joseph', 'Avignon', '0490145600'),
    ('0840004Y', 'Lycée Théodore Aubanel', 'Avignon', '0490163600'),
    ('0840110N', "Lycée d'enseignement général et technologique François Pétrarque", 'Avignon', '0490134313'),
    ('0840940R', 'Lycée polyvalent La Salle', 'Avignon', '0490145656'),
    ('0840005Z', 'Lycée polyvalent Philippe de Girard', 'Avignon', '0413951000'),
    ('0840935K', 'Lycée polyvalent René Char', 'Avignon', '0490880404'),
    ('0840041N', 'Lycée professionnel Maria Casarès', 'Avignon', '0490134545'),
    ('0840082H', 'Lycée professionnel Vincent de Paul', 'Avignon', '0490272390'),
    ('0840042P', 'SEP Robert Schuman', 'Avignon', '0413951000'),
    ('0840939P', "Section d'enseignement professionnel LPO René Char", 'Avignon', '0490880404'),
    ('0841230F', 'TSGE PR HC - COURS PRIVE PYTHAGORE', 'Avignon', '0432763562'),
    ('0040003G', 'Lycée polyvalent André Honnorat', 'Barcelonnette', '0492807010'),
    ('0040532G', "Section d'enseignement professionnel du lycée polyvalent André Honnorat", 'Barcelonnette', '0492807010'),
    ('0841093G', 'Lycée Lucie Aubrac', 'Bollène', '0432803190'),
    ('0050003B', "Lycée polyvalent d'altitude - Suzanne Joulié Roos", 'Briançon', '0492213084'),
    ('0050600A', "Section d'enseignement professionnel du Lycée polyvalent d'altitude Suzanne Joulié Roos ex CLIMATIQUE D'ALTITUDE", 'Briançon', '0492213084'),
    ('0133005W', 'AFASEC Académie de Cabriès ? Métiers des courses hippiques', 'Cabriès', '0442224145'),
    ('0840078D', 'Lycée Marie Pila', 'Carpentras', '0490630093'),
    ('0840607D', "Lycée d'enseignement général et technologique agricole Louis Giraud", 'Carpentras', '0490608080'),
    ('0840797K', "Lycée d'enseignement professionnel et technologique les Chênes", 'Carpentras', '0490630770'),
    ('0840015K', 'Lycée polyvalent Jean-Henri Fabre', 'Carpentras', '0490630583'),
    ('0840016L', 'Lycée polyvalent Victor Hugo', 'Carpentras', '0490631232'),
    ('0840044S', 'SEP. VICTOR HUGO', 'Carpentras', '0490631232'),
    ('0840954F', "Section d'enseignement professionnel du Lycée polyvalent Jean Henri Fabre", 'Carpentras', '0490630583'),
    ('0840017M', 'Lycée Ismaël Dauphin', 'Cavaillon', '0490710981'),
    ('0840113S', 'Lycée professionnel Alexandre Dumas', 'Cavaillon', '0490063450'),
    ('0134252B', "Lycée régional Jean D'Ormesson", 'Châteaurenard', '0490205950'),
    ('0040027H', 'Lycée Alexandra David-Néel', 'Digne-les-Bains', '0492303580'),
    ('0040490L', 'Lycée Pierre-Gilles de Gennes', 'Digne-les-Bains', '0492367190'),
    ('0040034R', 'Lycée polyvalent du Sacré-Coeur', 'Digne-les-Bains', '0492305860'),
    ('0040007L', 'Lycée professionnel Alphonse Beau de Rochas', 'Digne-les-Bains', '0492310608'),
    ('0050004C', 'Lycée Honoré Romane', 'Embrun', '0492431100'),
    ('0050005D', 'Lycée professionnel Alpes et Durance', 'Embrun', '0492431304'),
    ('0134000C', 'Sainte Victoire International School (LG PR HC)', 'Fuveau', '0647007672'),
    ('0050012L', 'Agricampus Hautes-Alpes', 'Gap', '0492510436'),
    ('0050007F', 'Lycée Aristide Briand', 'Gap', '0492522805'),
    ('0050006E', 'Lycée Dominique Villars', 'Gap', '0492522691'),
    ('0050035L', 'Lycée Saint-Joseph', 'Gap', '0492538444'),
    ('0050008G', 'Lycée professionnel Paul Héraud', 'Gap', '0492537484'),
    ('0050009H', 'Lycée professionnel Sévigné', 'Gap', '0492565610'),
    ('0131656E', "Lycée agricole d'Aix-Valabre", 'Gardanne', '0442654320'),
    ('0133244F', 'Lycée polyvalent Marie-Madeleine Fourcade', 'Gardanne', '0442659070'),
    ('0130025G', 'Lycée polyvalent Marie-Madeleine Fourcade - site Jules Ferry', 'Gardanne', '0442126430'),
    ('0133314G', 'Lycée Saint-Louis-Sainte-Marie', 'Gignac-la-Nerthe', '0442317300'),
    ('0133822J', 'Lycée Saint-Jean de Garguier', 'Gémenos', '0442188818'),
    ('0134430V', 'LP PR HC UIMM ALPES MEDITERRANEE', 'Istres', '0607674077'),
    ('0132495S', 'Lycée Arthur Rimbaud', 'Istres', '0442411096'),
    ('0132276D', 'Lycée professionnel Pierre Latécoère', 'Istres', '0442411950'),
    ('0840021S', 'Lycée polyvalent Alphonse Benoît', "L'Isle-sur-la-Sorgue", '0490206420'),
    ('0840606C', 'Lycée professionnel agricole La Ricarde', "L'Isle-sur-la-Sorgue", '0490380335'),
    ('0840953E', "Section d'enseignement professionnel du Lycée polyvalent Alphonse Benoit", "L'Isle-sur-la-Sorgue", '0490206420'),
    ('0840788A', 'Centre forestier de la région PACA', 'La Bastide-des-Jourdans', '0490778001'),
    ('0131747D', 'Lycée polyvalent Auguste et Louis Lumière', 'La Ciotat', '0442083838'),
    ('0133406G', 'Lycée polyvalent de la Méditerranée', 'La Ciotat', '0442088020'),
    ('0133413P', "Section d'enseignement professionnel du Lycée polyvalent Auguste et Louis Lumière", 'La Ciotat', '0442083838'),
    ('0133412N', "Section d'enseignement professionnel du Lycée polyvalent de la Méditerranée", 'La Ciotat', '0442088020'),
    ('0040056P', 'Lycée agricole Carmejane - Maurice Plantier', 'Le Chaffaut-Saint-Jurson', '0492303570'),
    ('0133961K', 'ECOLE SILVYA TERRADE - MIRABEAU (LP PR HC)', 'Les Pennes-Mirabeau', '0442092319'),
    ('0040550B', 'ECOLE PRIVE VICENTA ESTHETIQUE ET COIFFURE (LP PR HC)', 'Manosque', '0492741976'),
    ('0040496T', 'Institut Avenir Provence', 'Manosque', '0492874610'),
    ('0040010P', 'Lycée Félix Esclangon', 'Manosque', '0492705470'),
    ('0040533H', 'Lycée polyvalent Les Iscles', 'Manosque', '0492734110'),
    ('0040011R', 'Lycée professionnel Louis Martin Bret', 'Manosque', '0492707840'),
    ('0040534J', "Section d'enseignement professionnel du Lycée polyvalent Les Iscles", 'Manosque', ''),
    ('0040587S', "École internationale Provence-Alpes-Côte d'Azur", 'Manosque', '0492742311'),
    ('0132410Z', 'Lycée polyvalent Maurice Genevoix', 'Marignane', '0442887690'),
    ('0130033R', 'Lycée professionnel Louis Blériot', 'Marignane', '0442093050'),
    ('0132319A', 'S.E.P. MAURICE GENEVOIX', 'Marignane', '0442887690'),
    ('0133555U', 'Lycée Ami', 'Marseille', '0496100850'),
    ('0133286B', 'Lycée Belsunce', 'Marseille', '0491905114'),
    ('0131335F', 'Lycée Chevreul - Blancarde', 'Marseille', '0491491073'),
    ('0131344R', 'Lycée Cours Bastide', 'Marseille', '0491486796'),
    ('0133446A', 'Lycée Hamaskaïne', 'Marseille', '0491937525'),
    ('0130175V', 'Lycée Honoré Daumier', 'Marseille', '0491760120'),
    ('0131684K', 'Lycée Jeanne Perrimond', 'Marseille', '0491528307'),
    ('0131345S', "Lycée L'Olivier - Robert Coffy", 'Marseille', '0491939550'),
    ('0131324U', 'Lycée Lacordaire', 'Marseille', '0491122080'),
    ('0130037V', 'Lycée Marcel Pagnol', 'Marseille', '0491876400'),
    ('0130038W', 'Lycée Marseilleveyre', 'Marseille', '0491176700'),
    ('0131398Z', 'Lycée Maximilien de Sully', 'Marseille', '0491482787'),
    ('0130042A', 'Lycée Montgrand', 'Marseille', '0496112530'),
    ('0131333D', 'Lycée Notre-Dame de France', 'Marseille', '0491371755'),
    ('0131341M', 'Lycée Notre-Dame de Sion', 'Marseille', '0491157450'),
    ('0133931C', 'Lycée Notre-Dame de la Viste', 'Marseille', '0491609057'),
    ('0131328Y', 'Lycée Paul Melizan', 'Marseille', '0491188070'),
    ('0130036U', 'Lycée Périer', 'Marseille', '0491133900'),
    ('0130039X', 'Lycée Saint-Charles', 'Marseille', '0491082050'),
    ('0131342N', 'Lycée Saint-Charles Camas', 'Marseille', '0495081240'),
    ('0130048G', 'Lycée Saint-Exupéry', 'Marseille', '0491096900'),
    ('0131339K', 'Lycée Saint-Joseph de la Madeleine', 'Marseille', '0496121360'),
    ('0131331B', 'Lycée Saint-Joseph les Maristes', 'Marseille', '0496101330'),
    ('0131347U', 'Lycée Sainte-Trinité', 'Marseille', '0491411198'),
    ('0131348V', 'Lycée Sévigné', 'Marseille', '0491662275'),
    ('0130040Y', 'Lycée Thiers', 'Marseille', '0491189218'),
    ('0130043B', 'Lycée Victor Hugo', 'Marseille', '0491110500'),
    ('0132472S', 'Lycée Yavné', 'Marseille', '0491661477'),
    ('0134472R', 'Lycée cité internationale Jacques Chirac', 'Marseille', '0486830940'),
    ('0131323T', 'Lycée de Provence', 'Marseille', '0491772846'),
    ('0131327X', 'Lycée de Tour Sainte', 'Marseille', '0491215300'),
    ('0131681G', 'Lycée de chimie-biologie La Forbine', 'Marseille', '0491446048'),
    ('0131606A', 'Lycée des métiers La Calade - Jane Vialle', 'Marseille', '0491658650'),
    ('0134107U', 'Lycée général Ibn Khaldoun', 'Marseille', '0491489568'),
    ('0133334D', 'Lycée général et technologique ORT Léon Bramson', 'Marseille', '0491296133'),
    ('0134250Z', 'Lycée général privé Bnei Elazar', 'Marseille', '0491202913'),
    ('0132733A', 'Lycée polyvalent Antonin Artaud', 'Marseille', '0491122250'),
    ('0131402D', 'Lycée polyvalent Charles Péguy', 'Marseille', '0491157640'),
    ('0130050J', 'Lycée polyvalent Denis Diderot', 'Marseille', '0491100700'),
    ('0133396W', 'Lycée polyvalent Don Bosco', 'Marseille', '0491140000'),
    ('0130053M', 'Lycée polyvalent Jean Perrin', 'Marseille', '0491742930'),
    ('0132828D', 'Lycée polyvalent La Cadenelle', 'Marseille', '0491181050'),
    ('0133474F', 'Lycée polyvalent Marie Gasquet', 'Marseille', '0491851081'),
    ('0134003F', 'Lycée polyvalent Nelson Mandela', 'Marseille', '0491180250'),
    ('0131456M', 'Lycée polyvalent Pastré - Grande Bastide', 'Marseille', '0496190606'),
    ('0131403E', 'Lycée polyvalent Saint-Vincent de Paul', 'Marseille', '0491374886'),
    ('0134155W', 'Lycée polyvalent Simone Veil', 'Marseille', '0491815911'),
    ('0132974M', 'Lycée polyvalent hôtelier régional Jean-Paul Passedat', 'Marseille', '0491734781'),
    ('0131436R', 'Lycée polyvalent modèle électronique', 'Marseille', '0491446537'),
    ('0134101M', 'Lycée polyvalent privé Saint-Louis', 'Marseille', '0491658820'),
    ('0131463V', 'Lycée professionnel Alexandre Gueidon', 'Marseille', '0491214460'),
    ('0130072H', 'Lycée professionnel Ampère', 'Marseille', '0491298400'),
    ('0130059U', 'Lycée professionnel Blaise Pascal', 'Marseille', '0491180340'),
    ('0130068D', 'Lycée professionnel Camille Jullian', 'Marseille', '0491888300'),
    ('0131445A', 'Lycée professionnel Charlotte Grawitz', 'Marseille', '0491666386'),
    ('0130071G', 'Lycée professionnel Colbert', 'Marseille', '0491310452'),
    ('0132193N', 'Lycée professionnel Edmond Rostand', 'Marseille', '0491374375'),
    ('0130062X', 'Lycée professionnel Frédéric Mistral', 'Marseille', '0491291200'),
    ('0130054N', 'Lycée professionnel Germaine Poinso-Chapuis', 'Marseille', '0491167700'),
    ('0132790M', 'Lycée professionnel Jacques Raynaud', 'Marseille', '0491663940'),
    ('0130064Z', 'Lycée professionnel Jean-Baptiste Brochier', 'Marseille', '0491178830'),
    ('0130063Y', 'Lycée professionnel Leau', 'Marseille', '0491163710'),
    ('0131424C', 'Lycée professionnel ORT Léon Bramson', 'Marseille', '0491296133'),
    ('0131437S', 'Lycée professionnel Phocea René Attoyan', 'Marseille', '0491375365'),
    ('0130057S', 'Lycée professionnel René Caillié', 'Marseille', '0491181006'),
    ('0131432L', 'Lycée professionnel Saint-André (les Routiers)', 'Marseille', '0491460540'),
    ('0131433M', 'Lycée professionnel Saint-Henri', 'Marseille', '0495061095'),
    ('0131441W', 'Lycée professionnel Saint-Michel', 'Marseille', '0496125160'),
    ('0131848N', 'Lycée professionnel agricole des Calanques', 'Marseille', '0491727070'),
    ('0130058T', "Lycée professionnel et technologique régional L'Estaque", 'Marseille', '0495069070'),
    ('0130056R', 'Lycée professionnel la Floride', 'Marseille', '0495053535'),
    ('0130065A', 'Lycée professionnel la Viste', 'Marseille', '0491659040'),
    ('0130055P', 'Lycée professionnel le Chatelier', 'Marseille', '0495045500'),
    ('0131434N', 'Lycée professionnel École libre de métiers', 'Marseille', '0491424502'),
    ('0130049H', 'Lycée régional César Baldaccini (anciennement lycée du Rempart)', 'Marseille', '0491143280'),
    ('0130051K', 'Lycée technologique régional Marie Curie', 'Marseille', '0491365210'),
    ('0131515B', 'ECOLE SILVYA TERRADE - MARSEILLE (LP PR HC)', 'Marseille  6e  Arrondissement', '0491472250'),
    ('0134522V', 'Lycée GT PR.HC HEDER KEHILA LECHEM CHAMAIM (KLC)', 'Marseille  6e  Arrondissement', '0484188953'),
    ('0130172S', 'SEP LEONARD DE VINCI', 'Marseille  7e  Arrondissement', '0491140140'),
    ('0133683H', 'Ecole de coiffure Leader  (Ecole secondaire professionnelle privée)', 'Marseille  8e  Arrondissement', '0496203434'),
    ('0134514L', 'LYC PR  HAYA MOUCHKA', 'Marseille  8e  Arrondissement', '0491453334'),
    ('0133366N', "Section d'enseignement professionnel du Lycée polyvalent lycée des métiers Hôtelier Régional", 'Marseille  8e  Arrondissement', '0491734781'),
    ('0134038U', 'ECOLE PRIVE PIGIER LE COURS MESSIDORO (LP PR HC)', 'Marseille 10e  Arrondissement', '0495051880'),
    ('0132965C', 'Ecole privée Beth-Myriam (Ecole secondaire générale)', 'Marseille 10e  Arrondissement', '0491757104'),
    ('0134605K', 'LYCEE GENERAL PRIVE HORS CONTRAT COLLEL', 'Marseille 10e  Arrondissement', ''),
    ('0133364L', "Section d'enseignement professionnel du Lycée polyvalent Jean Perrin", 'Marseille 10e  Arrondissement', '0491742930'),
    ('0133588E', "Section d'enseignement général et technologique du Lycée professionnel René Caillié", 'Marseille 11e  Arrondissement', '0491181006'),
    ('0133013E', "Ecole privée L'Olympique de Marseille (Ecole secondaire professionnelle)", 'Marseille 12e  Arrondissement', '0491934077'),
    ('0134005H', "Section d'Enseignement professionnel du Lycée Polyvalent régional Nelson MANDELA", 'Marseille 12e  Arrondissement', '0491180250'),
    ('0134470N', 'ECOLE DE PRODUCTION NRSUD', 'Marseille 13e  Arrondissement', '0675628774'),
    ('0134090A', 'Etablissement secondaire privé Gan Mordekhai (LP PR HC)', 'Marseille 13e  Arrondissement', '0491060061'),
    ('0133414R', "Section d'enseignement professionnel du Lycée polyvalent Denis Diderot", 'Marseille 13e  Arrondissement', '0491100700'),
    ('0134435A', 'Ecole de production Fask Académy (LP Spé PR HC)', 'Marseille 14e  Arrondissement', '0623784004'),
    ('0133630A', "Section d'enseignement général et technologique du Lycée professionnel Jane Vialle ex LP CALADE (LA)", 'Marseille 15e  Arrondissement', '0491658650'),
    ('0133404E', 'Centre National des Arts Techniques (LP PR HC)', 'Martigues', '0442421248'),
    ('0132210G', 'Lycée polyvalent Jean Lurçat', 'Martigues', '0442413180'),
    ('0130143K', 'Lycée polyvalent Paul Langevin', 'Martigues', '0442800875'),
    ('0131484T', 'Lycée professionnel Brise-Lames', 'Martigues', '0442490049'),
    ('0132211H', "Section d'Enseignement Professionnel  Jean Lurçat", 'Martigues', '0442413180'),
    ('0133365M', "Section d'enseignement professionnel du Lycée polyvalent Paul Langevin", 'Martigues', '0442800875'),
    ('0133195C', 'Lycée Jean Cocteau', 'Miramas', '0490500298'),
    ('0131690S', "Lycée d'enseignement privé agricole Fontlongue", 'Miramas', '0490581846'),
    ('0130146N', 'Lycée professionnel les Alpilles', 'Miramas', '0490580295'),
    ('0840218F', 'Lycée Professionnel Agricole', 'Orange', '0490514800'),
    ('0840075A', 'Lycée Saint-Louis', 'Orange', '0490340150'),
    ('0840026X', "Lycée de l'Arc", 'Orange', '0490118300'),
    ('0840046U', 'Lycée professionnel Aristide Briand', 'Orange', '0490341218'),
    ('0840763Y', "Lycée professionnel de l'Argensol", 'Orange', '0490344610'),
    ('0840918S', 'Lycée polyvalent Val de Durance - Henri Silvy', 'Pertuis', '0490092500'),
    ('0840955G', "Section d'enseignement professionnel du Lycée polyvalent Henri Silvy ex LPO VAL DE DURANCE", 'Pertuis', '0490092500'),
    ('0132280H', 'Lycée polyvalent Henri Leroy', 'Port-Saint-Louis-du-Rhône', '0442860157'),
    ('0130151U', 'Lycée professionnel Charles Mongrand', 'Port-de-Bouc', '0442350470'),
    ('0130150T', 'Lycée professionnel Jean Moulin', 'Port-de-Bouc', '0442062403'),
    ('0130157A', 'Lycée professionnel les Ferrages', 'Saint-Chamas', '0490507036'),
    ('0050039R', 'Lycée professionnel Pierre et Louis Poutrain', 'Saint-Jean-Saint-Nicolas', '0492559228'),
    ('0134004G', 'Lycée Saint-Charles', 'Saint-Martin-de-Crau', '0490185903'),
    ('0131715U', 'Lycée professionnel agricole les Alpilles', 'Saint-Rémy-de-Provence', '0490920320'),
    ('0131518E', 'ECOLE SILVYA TERRADE - SALON (LP PR HC)', 'Salon-de-Provence', '0490562667'),
    ('0132922F', 'Lycée Saint-Jean', 'Salon-de-Provence', '0490532051'),
    ('0131360H', 'Lycée Viala Lacoste', 'Salon-de-Provence', '0490568969'),
    ('0130160D', "Lycée de l'Empéri", 'Salon-de-Provence', '0490447900'),
    ('0130161E', 'Lycée polyvalent Adam de Craponne', 'Salon-de-Provence', '0490562468'),
    ('0131485U', 'Lycée professionnel le Rocher', 'Salon-de-Provence', '0490561379'),
    ('0133554T', 'Lycée technologique privé Le Rocher', 'Salon-de-Provence', '0490561379'),
    ('0131709M', "Section d'enseignement professionnel du Lycée polyvalent Adam de Craponne", 'Salon-de-Provence', '0490562468'),
    ('0131675A', 'Lycée professionnel Sainte-Élisabeth', 'Septèmes-les-Vallons', '0491510141'),
    ('0040023D', 'Lycée polyvalent Paul Arène', 'Sisteron', '0492610299'),
    ('0040503A', "Section d'enseignement professionnel du Lycée polyvalent Paul Arène", 'Sisteron', '0492610299'),
    ('0841249B', 'Lycée général STEINER', 'Sorgues', '0490833707'),
    ('0841078R', 'Lycée professionnel Montesquieu', 'Sorgues', '0490397480'),
    ('0130164H', 'Lycée Alphonse Daudet', 'Tarascon', '0490911823'),
    ('0841117H', 'Lycée Stéphane Hessel', 'Vaison-la-Romaine', '0490360203'),
    ('0840746E', 'Lycée professionnel Acaf-Msa', 'Vaison-la-Romaine', '0490360141'),
    ('0841158C', 'Lycée Saint-Jean le Baptiste', 'Valréas', '0490350165'),
    ('0840700E', 'Lycée professionnel Ferdinand Revoul', 'Valréas', '0490350368'),
    ('0840747F', 'Lycée professionnel Saint-Jean le Baptiste', 'Valréas', '0490350340'),
    ('0840039L', "Lycée professionnel Domaine d'Eguilles", 'Vedène', '0490310715'),
    ('0050027C', 'Lycée professionnel Pierre Mendès-France', 'Veynes', '0492572345'),
    ('0133424B', 'Lycée polyvalent Caucadis', 'Vitrolles', '0442894202'),
    ('0133288D', 'Lycée polyvalent Jean Monnet', 'Vitrolles', '0442151460'),
    ('0133015G', 'Lycée polyvalent Pierre Mendès-France', 'Vitrolles', '0442898979'),
    ('0133487V', "Section d'enseignement professionnel du Lycée polyvalent Jean Monnet", 'Vitrolles', '0442151460'),
    ('0133367P', "Section d'enseignement professionnel du Lycée polyvalent Pierre Mendès France", 'Vitrolles', '0442898979'),
]

LYCEES_SHEET_NAME = "lycees"
LYCEES_N_DATA_ROWS = len(_LYCEES_AIM)  # 249
LYCEES_HEADERS = ["UAI", "Nom", "Ville", "Téléphone", "Etab"]

PREPS_HEADERS     = ["Matiere", "Matière court", "Temps preparation (min)", "Duree (min)"]
EXAM_HEADERS      = ["Nom", "Disc.poste", "Salle", "Heure mini", "Etab", "Loge"]
CANDIDATS_HEADERS = ["CANDIDAT", "CHOIX DISCIPLINE 1", "CHOIX DISCIPLINE 2", "TT", "Etab", "Profs"]


# ── Lecture ODS ───────────────────────────────────────────────────────────────

def _cell_text(cell) -> str:
    """Extrait le texte d'une cellule ODS."""
    parts = []
    for p in cell.getElementsByType(P):
        parts.append("".join(
            node.data for node in p.childNodes
            if hasattr(node, "data")
        ))
    return " ".join(parts).strip()


def _sheet_to_rows(table) -> list[dict]:
    """Convertit une Table ODS en list[dict] (1ère ligne = headers)."""
    all_rows_raw: list[list[str]] = []
    for row in table.getElementsByType(TableRow):
        cells = row.getElementsByType(TableCell)
        cols: list[str] = []
        for cell in cells:
            repeat = int(cell.getAttribute("numbercolumnsrepeated") or 1)
            val = _cell_text(cell)
            cols.extend([val] * repeat)
        # Supprime les colonnes répétées vides en fin de ligne
        while cols and cols[-1] == "":
            cols.pop()
        all_rows_raw.append(cols)

    # Supprime les lignes entièrement vides
    all_rows_raw = [r for r in all_rows_raw if any(c for c in r)]
    if not all_rows_raw:
        return []

    headers = [h.strip() for h in all_rows_raw[0]]
    rows: list[dict] = []
    for raw in all_rows_raw[1:]:
        row_dict = {}
        for i, h in enumerate(headers):
            row_dict[h] = raw[i].strip() if i < len(raw) else ""
        rows.append(row_dict)
    return rows


def parse_ods(data: bytes) -> dict[str, list[dict]]:
    """
    Parse un fichier ODS et retourne un dict {nom_feuille_normalisé: list[dict]}.
    Les noms de feuilles sont normalisés en minuscules.
    Lève ValueError si le fichier est illisible.
    """
    try:
        doc = odf_load(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Impossible de lire le fichier ODS : {exc}") from exc

    result: dict[str, list[dict]] = {}
    for table in doc.spreadsheet.getElementsByType(Table):
        name = table.getAttribute("name") or ""
        key = name.strip().lower()
        result[key] = _sheet_to_rows(table)
    return result


# ── Génération ODS modèle ─────────────────────────────────────────────────────

def _make_cell(doc: OpenDocumentSpreadsheet, value: str,
               style_name: str | None = None) -> TableCell:
    kwargs: dict = {"valuetype": "string"}
    if style_name:
        kwargs["stylename"] = style_name
    cell = TableCell(**kwargs)
    cell.addElement(P(text=value))
    return cell


def _make_number_cell(doc: OpenDocumentSpreadsheet, value: int | str) -> TableCell:
    cell = TableCell(valuetype="float", value=str(value))
    cell.addElement(P(text=str(value)))
    return cell


def _make_formula_cell(formula: str, cached_text: str = "") -> TableCell:
    """Cellule avec formule ODF (résultat de type string) et valeur cachée."""
    cell = TableCell(valuetype="string", formula=formula)
    if cached_text:
        cell.addElement(P(text=cached_text))
    return cell


def _add_header_style(doc: OpenDocumentSpreadsheet) -> str:
    style = Style(name="HeaderCell", family="table-cell")
    style.addElement(TextProperties(fontweight="bold"))
    style.addElement(TableCellProperties(
        backgroundcolor="#D9E1F2",
        border="0.5pt solid #4472C4",
    ))
    doc.automaticstyles.addElement(style)
    return "HeaderCell"


def _build_validation_list(names: list[str]) -> str:
    """Formate une liste de valeurs pour ODS content-validation."""
    quoted = [f'"{v}"' for v in names]
    return ";".join(quoted)


def generate_ods_modele(preps_rows: list[dict] | None = None) -> bytes:
    """
    Génère le fichier ODS modèle avec 4 feuilles :
      - candidats    : en-têtes + validation disciplines / TT / Etab (lycees)
      - examinateurs : en-têtes + validation disciplines / heure / Etab (lycees)
      - preps        : pré-remplie (données ou valeurs par défaut)
      - lycees       : 249 lycées académie Aix-Marseille avec formule Ville—Nom (UAI)
    Retourne les bytes du fichier .ods.
    """
    doc = OpenDocumentSpreadsheet()
    header_style = _add_header_style(doc)

    # ── Feuille preps ──────────────────────────────────────────────────────────
    sheet_preps = Table(name="preps")

    # En-tête
    hr = TableRow()
    for h in PREPS_HEADERS:
        hr.addElement(_make_cell(doc, h, header_style))
    sheet_preps.addElement(hr)

    # Données
    if preps_rows:
        data_rows = [
            (
                r.get("Matiere", ""),
                r.get("Matière court", ""),
                r.get("Temps preparation (min)", ""),
                r.get("Duree (min)", ""),
            )
            for r in preps_rows
            if r.get("Matiere")
        ]
    else:
        data_rows = [(m, c, str(t), str(d)) for m, c, t, d in _DEFAULT_PREPS]

    short_names = [row[1] for row in data_rows if row[1]]

    for mat, court, tprep, duree in data_rows:
        row = TableRow()
        row.addElement(_make_cell(doc, mat))
        row.addElement(_make_cell(doc, court))
        try:
            row.addElement(_make_number_cell(doc, int(tprep)))
        except (ValueError, TypeError):
            row.addElement(_make_cell(doc, str(tprep)))
        try:
            row.addElement(_make_number_cell(doc, int(duree)))
        except (ValueError, TypeError):
            row.addElement(_make_cell(doc, str(duree)))
        sheet_preps.addElement(row)

    # ── Feuille lycees ─────────────────────────────────────────────────────────
    sheet_lycees = Table(name=LYCEES_SHEET_NAME)

    hr_l = TableRow()
    for h in LYCEES_HEADERS:
        hr_l.addElement(_make_cell(doc, h, header_style))
    sheet_lycees.addElement(hr_l)

    for i, (uai, nom, ville, tel) in enumerate(_LYCEES_AIM):
        # Ligne ODS = i+2 (la ligne 1 est l'en-tête)
        ods_row = i + 2
        formula = f"of:=C{ods_row}&\" — \"&B{ods_row}&\" (\"&A{ods_row}&\")\""
        cached = f"{ville} — {nom} ({uai})"
        row = TableRow()
        row.addElement(_make_cell(doc, uai))
        row.addElement(_make_cell(doc, nom))
        row.addElement(_make_cell(doc, ville))
        row.addElement(_make_cell(doc, tel))
        row.addElement(_make_formula_cell(formula, cached))
        sheet_lycees.addElement(row)

    # ── Validations ODS ────────────────────────────────────────────────────────
    validations = ContentValidations()
    doc.spreadsheet.addElement(validations)

    disc_list = _build_validation_list(short_names) if short_names else '""'
    tt_list   = _build_validation_list(["0", "1"])
    # Validation Etab : liste déroulante depuis la colonne E (Etab) de la feuille lycees
    etab_range = (
        f"of:cell-content-is-in-list(${LYCEES_SHEET_NAME}.$E$2:"
        f"${LYCEES_SHEET_NAME}.$E${LYCEES_N_DATA_ROWS + 1})"
    )

    val_disc = ContentValidation(
        name="vDisc",
        condition=f"of:cell-content-is-in-list({disc_list})",
        allowemptycell="true",
        displaylist="unsorted",
    )
    validations.addElement(val_disc)

    val_tt = ContentValidation(
        name="vTT",
        condition=f"of:cell-content-is-in-list({tt_list})",
        allowemptycell="true",
        displaylist="unsorted",
    )
    validations.addElement(val_tt)

    val_heure = ContentValidation(
        name="vHeure",
        condition="of:cell-content-is-between(0;23)",
        allowemptycell="true",
    )
    validations.addElement(val_heure)

    val_etab = ContentValidation(
        name="vEtab",
        condition=etab_range,
        allowemptycell="true",
        displaylist="sorted-ascending",
    )
    validations.addElement(val_etab)

    # ── Feuille candidats ─────────────────────────────────────────────────────
    # CANDIDAT(0) CHOIX1(1) CHOIX2(2) TT(3) Etab(4) Profs(5)
    sheet_cands = Table(name="candidats")
    doc.spreadsheet.addElement(sheet_cands)

    cand_col_validations = {1: "vDisc", 2: "vDisc", 3: "vTT", 4: "vEtab"}

    hr3 = TableRow()
    for h in CANDIDATS_HEADERS:
        hr3.addElement(_make_cell(doc, h, header_style))
    sheet_cands.addElement(hr3)

    _add_empty_rows_with_validation(doc, sheet_cands, CANDIDATS_HEADERS, cand_col_validations, 200)

    # ── Feuille examinateurs ──────────────────────────────────────────────────
    # Nom(0) Disc.poste(1) Salle(2) Heure mini(3) Etab(4) Loge(5)
    sheet_exam = Table(name="examinateurs")
    doc.spreadsheet.addElement(sheet_exam)

    exam_col_validations = {1: "vDisc", 3: "vHeure", 4: "vEtab"}

    hr2 = TableRow()
    for h in EXAM_HEADERS:
        hr2.addElement(_make_cell(doc, h, header_style))
    sheet_exam.addElement(hr2)

    _add_empty_rows_with_validation(doc, sheet_exam, EXAM_HEADERS, exam_col_validations, 50)

    # ── Feuille preps (ajout au doc après candidats/examinateurs) ─────────────
    doc.spreadsheet.addElement(sheet_preps)
    doc.spreadsheet.addElement(sheet_lycees)

    # Sérialisation
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _add_empty_rows_with_validation(
    doc: OpenDocumentSpreadsheet,
    sheet: Table,
    headers: list[str],
    col_validations: dict[int, str],
    n_rows: int,
) -> None:
    """Ajoute n_rows lignes vides avec validation sur les colonnes indiquées."""
    for _ in range(n_rows):
        row = TableRow()
        for col_idx in range(len(headers)):
            if col_idx in col_validations:
                cell = TableCell(valuetype="string",
                                 contentvalidationname=col_validations[col_idx])
            else:
                cell = TableCell(valuetype="string")
            row.addElement(cell)
        sheet.addElement(row)
