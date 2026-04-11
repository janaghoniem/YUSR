"""
task_memory.py

ChromaDB-backed Task Memory for the Action Agent.
Replaces action_knowledge_base.py entirely.

Architecture:
    - One ChromaDB collection: "golden_paths"
    - Each record = one atomic step from either the AndroidControl dataset or a live run
    - Embedding: composite of overall_goal + step_instruction via BAAI/bge-small-en-v1.5
    - Three retrieval bands at runtime:
        >= 0.90  →  execute as guided script (verify each element before acting)
        0.70–0.89 →  inject as hint context into Tier 3 LLM prompt
        < 0.70   →  no hint, pure Tier 3 ReAct from scratch

    On successful Tier 3 execution the agent stores the new steps back here,
    linked to the overall_goal, so the system compounds knowledge over time.

Dataset loading:
    On first init, if the collection is empty, auto-populates from
    JSONL_PATH (cleaned_recipes.jsonl from AndroidControl processing).
    Subsequent inits just warm up the in-process embedding model.

Debug logging:
    All cache-related events are prefixed [CACHE] for easy grep/filtering.
"""
"""
pkg_normalization_patch.py
==========================
Drop this entire block into task_memory.py, replacing whatever
_APP_PKG_NORMALIZE / _normalize_app you had before.

After adding it:
  1. Delete your ChromaDB directory (task_memory_db/)
  2. Restart the server — TaskMemory.__init__ will reimport everything
     with the corrected app values.

Covers every package ID in the AndroidControl dataset spreadsheet.
74 packages are DROPPED entirely (system internals, moon-phase apps,
completely unidentifiable single-vendor tools).
767 packages are kept and mapped to 71 canonical category strings
that match what the coordinator puts in extra_params["app_name"].
"""

from typing import Dict, Set
import dataclasses
# ── Complete package → category map ────────────────────────────────────────
# Canonical category strings are chosen to match common coordinator
# extra_params["app_name"] values so cache queries hit without aliasing.

_PKG_CATEGORY_MAP: Dict[str, str] = {
    "aarnav100.developer.g_forms":                                              "g_forms",
    "ae.propertyfinder.propertyfinder":                                         "property_finder",
    "ai.iplan.app":                                                             "iplan",
    "alarm.clock.calendar.reminder":                                            "alarm_clock",
    "all.in.one.calculator":                                                    "calculator",
    "app.cybrook.teamlink":                                                     "teamlink",
    "app.marcheretail":                                                         "marche_retail",
    "app.martinoz.pizza":                                                       "food",
    "app.meditasyon":                                                           "meditation",
    "app.plantora.plantora":                                                    "plant",
    "appyweather.appyweather":                                                  "weather",
    "ar.com.basejuegos.simplealarm":                                            "alarm_clock",
    "art.tcm.artsticker":                                                       "art",
    "au.com.opal.travel":                                                       "travel",
    "bbc.mobile.news.ww":                                                       "news",
    "booksfortune.bookchor":                                                    "books",
    "br.com.caiocrol.alarmandpillreminder":                                     "alarm_clock",
    "br.com.maxmilhas":                                                         "travel",
    "br.com.netshoes.app":                                                      "shopping",
    "br.com.zattini":                                                           "shopping",
    "calendar.agenda.calendarplanner.agendaplanner":                            "calendar",
    "ch.coop.coopapp":                                                          "shopping",
    "ch.migros.app":                                                            "shopping",
    "ch.mnc.zvv.oneapp":                                                        "transit",
    "ch.sbb.mobile.android.b2c":                                                "transit",
    "chotelal.mpaani.com.android.chotelal":                                     "shopping",
    "cn.wps.moffice_eng":                                                       "office",
    "cn.xiaofengkj.fitpro":                                                     "fitness",
    "co.climacell.climacell":                                                   "weather",
    "co.cruisedealsapp.aronbeaver.cruisedeals":                                 "travel",
    "co.peppertheapp.android":                                                  "shopping",
    "co.shopney.zappobrands":                                                   "shopping",
    "co.windyapp.android":                                                      "weather",
    "com.Clairvoyant.FernsAndPetals":                                           "shopping",
    "com.Dominos":                                                              "food",
    "com.Meditation.app":                                                       "meditation",
    "com.RanaSourav.android.notes":                                             "notes",
    "com.ToDoReminder.gen":                                                     "todo",
    "com.abercrombie.abercrombie":                                              "shopping",
    "com.absut.car":                                                            "automotive",
    "com.accurate.local.live.weather":                                          "weather",
    "com.accurate.local.weather.forecast.live":                                 "weather",
    "com.accuweather.android":                                                  "weather",
    "com.adanione.android":                                                     "shopping",
    "com.adidas.app":                                                           "shopping",
    "com.adobe.fas":                                                            "productivity",
    "com.adobe.reader":                                                         "pdf",
    "com.adobe.scan.android":                                                   "scanner",
    "com.adsk.sketchbook":                                                      "art",
    "com.afklm.mobile.android.gomobile.klm":                                   "travel",
    "com.afmobi.boomplayer":                                                    "music",
    "com.agoda.mobile.consumer":                                                "travel",
    "com.agrico.trail3":                                                        "outdoor",
    "com.aige.hipaint":                                                         "art",
    "com.airbnb.android":                                                       "airbnb",
    "com.airgoat.goat":                                                         "shopping",
    "com.ajnsnewmedia.kitchenstories":                                          "recipes",
    "com.alarmclock.xtreme.free":                                               "alarm_clock",
    "com.alfuttaim.digital.tru":                                                "shopping",
    "com.algeo.algeo":                                                          "calculator",
    "com.alibaba.intl.android.apps.poseidon":                                   "shopping",
    "com.allensolly.abfrl":                                                     "shopping",
    "com.alltrails.alltrails":                                                  "outdoor",
    "com.alp.allrecipes":                                                       "recipes",
    "com.amazon.kindle":                                                        "books",
    "com.amila.parenting":                                                      "parenting",
    "com.amtrak.rider":                                                         "transit",
    "com.android.chrome":                                                       "chrome",
    "com.android.settings":                                                     "settings",
    "com.android.vending":                                                      "play_store",
    "com.andronicus.ledclock":                                                  "clock",
    "com.andrwq.recorder":                                                      "recorder",
    "com.anghami":                                                              "music",
    "com.anydo":                                                                "todo",
    "com.apalon.weatherlive.free":                                              "weather",
    "com.app.character":                                                        "ai",
    "com.app.pepperfry":                                                        "shopping",
    "com.app.sugarcosmetics":                                                   "shopping",
    "com.appgeneration.itunerfree":                                             "music",
    "com.application.zomato":                                                   "food",
    "com.applications.homecentre":                                              "shopping",
    "com.applications.lifestyle":                                               "shopping",
    "com.applications.max":                                                     "shopping",
    "com.applus.notepad.note":                                                  "notes",
    "com.apps.sportsbazar":                                                     "shopping",
    "com.arriva.glimble":                                                       "transit",
    "com.arthurivanets.reminder":                                               "reminders",
    "com.asos.app":                                                             "shopping",
    "com.atistudios.mondly.languages":                                          "language_learning",
    "com.atomczak.notepat":                                                     "notes",
    "com.atrule.timezonenotify":                                                "clock",
    "com.audiomack":                                                            "music",
    "com.autoscout24":                                                          "automotive",
    "com.b2w.shoptime":                                                         "shopping",
    "com.babbel.mobile.android.en":                                             "language_learning",
    "com.babycenter.pregnancytracker":                                          "parenting",
    "com.ballistiq.artstation":                                                 "art",
    "com.banggood.client":                                                      "shopping",
    "com.barakat":                                                              "shopping",
    "com.barnesandnoble.app":                                                   "books",
    "com.behance.behance":                                                      "art",
    "com.bekism.contacts.pro":                                                  "contacts",
    "com.bendingspoons.thirtydayfitness":                                       "fitness",
    "com.better.alarm":                                                         "alarm_clock",
    "com.bewakoof.bewakoof":                                                    "shopping",
    "com.bhsoft.timebuddy":                                                     "clock",
    "com.bigbasket.mobileapp":                                                  "grocery",
    "com.bigoven.android":                                                      "recipes",
    "com.blaze.wordprocessor":                                                  "office",
    "com.blintzpizzauserreactapp":                                              "food",
    "com.bng.calculator":                                                       "calculator",
    "com.bookaway.users":                                                       "travel",
    "com.booking":                                                              "travel",
    "com.bookmate":                                                             "books",
    "com.bookscape":                                                            "books",
    "com.boulla.sport_clothes":                                                 "shopping",
    "com.boulla.toys_shopping":                                                 "shopping",
    "com.box.android":                                                          "cloud_storage",
    "com.brakefield.painter":                                                   "art",
    "com.bsbportal.music":                                                      "music",
    "com.bsl.spencers.activity":                                                "shopping",
    "com.bublup.bublup":                                                        "notes",
    "com.busbud.android":                                                       "transit",
    "com.busuu.android.enc":                                                    "language_learning",
    "com.buzzfeed.tasty":                                                       "recipes",
    "com.bvblogic.nimbusnote":                                                  "notes",
    "com.calm.android":                                                         "meditation",
    "com.cars24.seller":                                                        "automotive",
    "com.cartrade.car":                                                         "automotive",
    "com.carwale":                                                              "automotive",
    "com.centr.app":                                                            "fitness",
    "com.cf.flightsearch":                                                      "travel",
    "com.chanel.weather.forecast.accu":                                         "weather",
    "com.chanelapp.chanel":                                                     "shopping",
    "com.changdu.ereader":                                                      "books",
    "com.changecollective.tenpercenthappier":                                   "meditation",
    "com.channelnewsasia":                                                      "news",
    "com.cheapflightsapp.flightbooking":                                        "travel",
    "com.chegal.alarm":                                                         "alarm_clock",
    "com.cisco.webex.meetings":                                                 "video_call",
    "com.cisco.wx2.android":                                                    "video_call",
    "com.citymapper.app.release":                                               "maps",
    "com.cleartrip.android":                                                    "travel",
    "com.cleevio.spendee":                                                      "finance",
    "com.cloudmagic.mail":                                                      "email",
    "com.cnn.mobile.android.phone":                                             "news",
    "com.coffeebeanventures.easyvoicerecorder":                                 "recorder",
    "com.coffye.epnksg":                                                        "food",
    "com.colourpopcosmetics.app":                                               "shopping",
    "com.companyname.MaturaMatematyka":                                         "education",
    "com.compscieddy.threethings":                                              "todo",
    "com.conceptivapps.blossom":                                                "plant",
    "com.contacts.contacts.freeapp":                                            "contacts",
    "com.contapps.android":                                                     "contacts",
    "com.cookd.app":                                                            "recipes",
    "com.cookware.lunchrecipes":                                                "recipes",
    "com.creativedrop.pizza_max":                                               "food",
    "com.crosscountrytrains":                                                   "transit",
    "com.crrepa.band.altfit":                                                   "fitness",
    "com.csgroup.texteditor":                                                   "notes",
    "com.cube.gdpc.fa":                                                         "health",
    "com.currency.converter.exchange.rate.money.free":                          "finance",
    "com.cxinventor.file.explorer":                                             "files",
    "com.dailyCart":                                                            "grocery",
    "com.dailymotion.dailymotion":                                              "video",
    "com.dci.magzter":                                                          "books",
    "com.dd.doordash":                                                          "food",
    "com.dddev.gallery.album.photo.editor":                                     "photos",
    "com.deep.smartcalculator":                                                 "calculator",
    "com.deliveroo.orderapp":                                                   "food",
    "com.dencreak.dlcalculator":                                                "calculator",
    "com.dencreak.esmemo":                                                      "notes",
    "com.desertcartnative":                                                     "shopping",
    "com.desmos.calculator":                                                    "calculator",
    "com.desygner.presentations":                                               "productivity",
    "com.developerhub.moneytracker":                                            "finance",
    "com.deviantart.android.damobile":                                          "art",
    "com.dhgate.buyermob":                                                      "shopping",
    "com.dictionary.words1":                                                    "dictionary",
    "com.digibites.calendar":                                                   "calendar",
    "com.digitalchemy.currencyconverter":                                       "finance",
    "com.digitalchemy.recorder":                                                "recorder",
    "com.dkapp.mathstables":                                                    "education",
    "com.dolby.dolby234":                                                       "music",
    "com.doodle.android":                                                       "productivity",
    "com.drawing.pad.desk.app.coloring.book.paint.sketch":                     "art",
    "com.droid4you.application.wallet":                                         "finance",
    "com.dropbox.android":                                                      "cloud_storage",
    "com.dubizzle.horizontal":                                                  "shopping",
    "com.dubox.drive":                                                          "cloud_storage",
    "com.dunzo.user":                                                           "grocery",
    "com.duocards.app":                                                         "language_learning",
    "com.duolingo":                                                             "duolingo",
    "com.dylvian.mango.activities":                                             "language_learning",
    "com.easemytrip.android":                                                   "travel",
    "com.easy.currency.extra.androary":                                         "finance",
    "com.easy.decompress.unrar.archiver":                                       "files",
    "com.easymobs.pregnancy":                                                   "parenting",
    "com.ebay.mobile":                                                          "shopping",
    "com.eco.note":                                                             "notes",
    "com.edmunds":                                                              "automotive",
    "com.edreams.travel":                                                       "travel",
    "com.edurev":                                                               "education",
    "com.einnovation.temu":                                                     "shopping",
    "com.electronicsbazaar.electronicsbazaar":                                  "shopping",
    "com.elevatelabs.geonosis":                                                 "education",
    "com.endless.cookbook":                                                     "recipes",
    "com.estee.lauder.beauty.app":                                              "shopping",
    "com.esteps.firstaid":                                                      "health",
    "com.eterno":                                                               "news",
    "com.etraveli.android":                                                     "travel",
    "com.etraveli.mytrip.android":                                              "travel",
    "com.etsy.android":                                                         "shopping",
    "com.euronews.express":                                                     "news",
    "com.eurostar.androidapp":                                                  "transit",
    "com.evamall.evacustomer":                                                  "shopping",
    "com.eventmanager.task.calendar":                                           "calendar",
    "com.evernote":                                                             "notes",
    "com.exovoid.weather.app":                                                  "weather",
    "com.exovoid.weatherxs":                                                    "weather",
    "com.expedia.bookings":                                                     "travel",
    "com.facebook.orca":                                                        "messaging",
    "com.farfetch.farfetchshop":                                                "shopping",
    "com.fbd.letterwriting":                                                    "productivity",
    "com.first75.voicerecorder2":                                               "recorder",
    "com.fitbit.FitbitMobile":                                                  "fitness",
    "com.fitifyworkouts.bodyweight.workoutapp":                                 "fitness",
    "com.fitnesskeeper.runkeeper.pro":                                          "fitness",
    "com.fitvate.gymworkout":                                                   "fitness",
    "com.fivestars.calendarpro.workplanner":                                    "calendar",
    "com.fivestars.supernote.colornotes":                                       "notes",
    "com.flipkart.android":                                                     "shopping",
    "com.flipkart.shopsy":                                                      "shopping",
    "com.flipsnackapp":                                                         "books",
    "com.florasense":                                                           "plant",
    "com.floweraura":                                                           "shopping",
    "com.flowers1800.androidapp2":                                              "shopping",
    "com.flyersoft.moonreader":                                                 "books",
    "com.foodienation.all":                                                     "food",
    "com.foodient.whisk":                                                       "recipes",
    "com.foodro.mobileapp":                                                     "food",
    "com.footshop.ftshp":                                                       "shopping",
    "com.formsapp":                                                             "productivity",
    "com.foxit.mobile.pdf.lite":                                                "pdf",
    "com.foxnews.android":                                                      "news",
    "com.freshtohome":                                                          "grocery",
    "com.fsn.nds":                                                              "shopping",
    "com.fsn.nykaa":                                                            "shopping",
    "com.fsn.nykaa.man":                                                        "shopping",
    "com.fsoydan.howistheweather":                                              "weather",
    "com.funda.two":                                                            "real_estate",
    "com.funeasylearn.languages":                                               "language_learning",
    "com.furlenco.android":                                                     "shopping",
    "com.fws.plantsnap2":                                                       "plant",
    "com.gaana":                                                                "music",
    "com.gallery.picturegallery.photomanager":                                  "photos",
    "com.getbybus.mobile":                                                      "transit",
    "com.getsomeheadspace.android":                                             "meditation",
    "com.gigaworks.tech.calculator":                                            "calculator",
    "com.giovesoft.frogweather":                                                "weather",
    "com.girnarsoft.cardekho":                                                  "automotive",
    "com.github.jamesgay.fitnotes":                                             "fitness",
    "com.globalsources.globalsources_app":                                      "shopping",
    "com.godrej.naturesbasketltd":                                              "grocery",
    "com.goeuro.rosie":                                                         "transit",
    "com.goibibo":                                                              "travel",
    "com.goodwy.dialer":                                                        "phone",
    "com.google.android.GoogleCamera":                                          "camera",
    "com.google.android.apps.books":                                            "books",
    "com.google.android.apps.cultural":                                         "art",
    "com.google.android.apps.docs":                                             "cloud_storage",
    "com.google.android.apps.docs.editors.docs":                                "google_docs",
    "com.google.android.apps.docs.editors.sheets":                             "google_sheets",
    "com.google.android.apps.docs.editors.slides":                             "google_slides",
    "com.google.android.apps.dynamite":                                         "messaging",
    "com.google.android.apps.fitness":                                          "fitness",
    "com.google.android.apps.healthdata":                                       "health",
    "com.google.android.apps.magazines":                                        "news",
    "com.google.android.apps.maps":                                             "maps",
    "com.google.android.apps.messaging":                                        "messages",
    "com.google.android.apps.nbu.files":                                        "files",
    "com.google.android.apps.photos":                                           "photos",
    "com.google.android.apps.photosgo":                                         "photos",
    "com.google.android.apps.recorder":                                         "recorder",
    "com.google.android.apps.tachyon":                                          "video_call",
    "com.google.android.apps.tasks":                                            "todo",
    "com.google.android.apps.translate":                                        "translate",
    "com.google.android.apps.youtube.music":                                    "youtube_music",
    "com.google.android.calculator":                                            "calculator",
    "com.google.android.calendar":                                              "calendar",
    "com.google.android.contacts":                                              "contacts",
    "com.google.android.deskclock":                                             "clock",
    "com.google.android.dialer":                                                "phone",
    "com.google.android.documentsui":                                           "files",
    "com.google.android.gm":                                                    "gmail",
    "com.google.android.googlequicksearchbox":                                  "google_search",
    "com.google.android.keep":                                                  "notes",
    "com.google.android.youtube":                                               "youtube",
    "com.google.earth":                                                         "maps",
    "com.gostor":                                                               "shopping",
    "com.grability.rappi":                                                      "food",
    "com.grailr.carrotweather":                                                 "weather",
    "com.greyhound.mobile.consumer":                                            "transit",
    "com.grofers.customerapp":                                                  "grocery",
    "com.groupon":                                                              "shopping",
    "com.grubhub.android":                                                      "food",
    "com.guardian":                                                             "news",
    "com.habitrpg.android.habitica":                                            "productivity",
    "com.handmark.expressweather":                                              "weather",
    "com.hatchbaby":                                                            "parenting",
    "com.hcom.android":                                                         "travel",
    "com.healofy":                                                              "parenting",
    "com.healthifyme.basic":                                                    "fitness",
    "com.heartfull.forms":                                                      "productivity",
    "com.hellotalk":                                                            "language_learning",
    "com.helloweatherapp":                                                      "weather",
    "com.here.app.maps":                                                        "maps",
    "com.hevy":                                                                 "fitness",
    "com.hikingproject.android":                                                "outdoor",
    "com.historyofart":                                                         "art",
    "com.hithink.teameet":                                                      "video_call",
    "com.hm.goe":                                                               "shopping",
    "com.holucent.math":                                                        "calculator",
    "com.homesnap":                                                             "real_estate",
    "com.homzmart":                                                             "shopping",
    "com.hopper.mountainview.play":                                             "travel",
    "com.houzz.app":                                                            "shopping",
    "com.hp.babyapp":                                                           "parenting",
    "com.hp.pregnancy.lite":                                                    "parenting",
    "com.hungama.myplay.activity":                                              "music",
    "com.idealista.android":                                                    "real_estate",
    "com.ifirstaid":                                                            "health",
    "com.igp.android":                                                          "shopping",
    "com.iherb":                                                                "shopping",
    "com.imobilize.relaxsleepwell":                                             "meditation",
    "com.inditex.massimodutti":                                                 "shopping",
    "com.inditex.pullandbear":                                                  "shopping",
    "com.inditex.zara":                                                         "shopping",
    "com.indolj.californiapizza":                                               "food",
    "com.infraware.office.link":                                                "office",
    "com.ingka.ikea.app":                                                       "shopping",
    "com.inkitt.android.hermione":                                              "books",
    "com.inomera.sm":                                                           "grocery",
    "com.ionicframework.cursosdegraca":                                         "education",
    "com.it.DTube":                                                             "video",
    "com.ixigo":                                                                "travel",
    "com.ixigo.train.ixitrain":                                                 "transit",
    "com.jd.jdsports":                                                          "shopping",
    "com.jee.calc":                                                             "calculator",
    "com.jeeb.user":                                                            "grocery",
    "com.jio.media.jiobeats":                                                   "music",
    "com.joelapenna.foursquared":                                               "maps",
    "com.jonathanpuckey.radiogarden":                                           "music",
    "com.joom":                                                                 "shopping",
    "com.jotform.v2":                                                           "productivity",
    "com.jpl.jiomart":                                                          "grocery",
    "com.jumia.android":                                                        "shopping",
    "com.kayak.android":                                                        "travel",
    "com.kgcart.shop":                                                          "shopping",
    "com.kickscrew.android":                                                    "shopping",
    "com.kingbrain.step.counter.heart.monitor.sleep.tracker.eye.workout.free":  "fitness",
    "com.klook":                                                                "travel",
    "com.kmo.pdf.editor":                                                       "pdf",
    "com.kobobooks.android":                                                    "books",
    "com.kokoschka.michael.weather":                                            "weather",
    "com.lafourchette.lafourchette":                                            "food",
    "com.lastminute":                                                           "travel",
    "com.lazada.android":                                                       "shopping",
    "com.linhoapps.sgraffito":                                                  "art",
    "com.liteforex.forexcurrencies":                                            "finance",
    "com.loco2.loco2":                                                          "transit",
    "com.locon.housing":                                                        "real_estate",
    "com.louisphilippe.abfrl":                                                  "shopping",
    "com.loyaltyplant.partner.papajohns":                                       "food",
    "com.lulu.in":                                                              "shopping",
    "com.luxuryestate.android":                                                 "real_estate",
    "com.mail.emailapp.easymail2018":                                           "email",
    "com.makemytrip":                                                           "travel",
    "com.manash.purplle":                                                       "shopping",
    "com.mart.weather":                                                         "weather",
    "com.mdev.tododo":                                                          "todo",
    "com.media.bestrecorder.audiorecorder":                                     "recorder",
    "com.meesho.supply":                                                        "shopping",
    "com.melodis.midomiMusicIdentifier.freemium":                               "music",
    "com.memrise.android.memrisecompanion":                                     "language_learning",
    "com.merriamwebster":                                                       "dictionary",
    "com.mg.smplan":                                                            "productivity",
    "com.mi.android.globalFileexplorer":                                        "files",
    "com.microsoft.amp.apps.bingweather":                                       "weather",
    "com.microsoft.lists.public":                                               "productivity",
    "com.microsoft.office.officehubrow":                                        "office",
    "com.microsoft.office.outlook":                                             "email",
    "com.microsoft.office.powerpoint":                                          "powerpoint",
    "com.microsoft.office.word":                                                "word",
    "com.microsoft.skydrive":                                                   "cloud_storage",
    "com.microsoft.todos":                                                      "todo",
    "com.microsoft.whiteboard.publicpreview":                                   "productivity",
    "com.milkbasket.app":                                                       "grocery",
    "com.mindvalley.mva":                                                       "education",
    "com.mmi.maps":                                                             "maps",
    "com.mns":                                                                  "shopping",
    "com.mobgen.smartify":                                                      "art",
    "com.mobile.simplilearn":                                                   "education",
    "com.mobiletranstorapps.all.languages.translator.free.voice.translation":   "translate",
    "com.mobisystems.editor.office_with_reg":                                   "office",
    "com.mobisystems.mobidrive":                                                "cloud_storage",
    "com.mobisystems.mobiscanner":                                              "scanner",
    "com.mobisystems.msdict.embedded.wireless.oxford.dictionaryofenglish":      "dictionary",
    "com.mobisystems.office":                                                   "office",
    "com.mobisystems.ubreader_west":                                            "books",
    "com.mobstac.thehindu":                                                     "news",
    "com.moglix.online":                                                        "shopping",
    "com.moiseum.dailyart2":                                                    "art",
    "com.mojopizza":                                                            "food",
    "com.momondo.flightsearch":                                                 "travel",
    "com.monefy.app.lite":                                                      "finance",
    "com.move.realtor":                                                         "real_estate",
    "com.moymer.falou":                                                         "language_learning",
    "com.mufumbo.android.recipe.search":                                        "recipes",
    "com.my.pdfnew":                                                            "pdf",
    "com.myfitnesspal.android":                                                 "fitness",
    "com.myglamm.ecommerce":                                                    "shopping",
    "com.myntra.android":                                                       "shopping",
    "com.myplantin.app":                                                        "plant",
    "com.mypustak":                                                             "books",
    "com.mysteriousanayasman.abaweather":                                       "weather",
    "com.mytowntonight.aviationweather":                                        "weather",
    "com.myworkoutplan.myworkoutplan":                                          "fitness",
    "com.namshi.android":                                                       "shopping",
    "com.naver.labs.translator":                                                "translate",
    "com.neosouqindia.bechdo":                                                  "shopping",
    "com.newspaperdirect.pressreader.android":                                  "news",
    "com.ng_labs.paint":                                                        "art",
    "com.nike.omega":                                                           "shopping",
    "com.nll.asr":                                                              "recorder",
    "com.nmbs":                                                                 "transit",
    "com.nnacres.app":                                                          "real_estate",
    "com.nnnow.arvind":                                                         "shopping",
    "com.nobroker.app":                                                         "real_estate",
    "com.noctuasoftware.stellarium_free":                                       "education",
    "com.noon.buyerapp":                                                        "shopping",
    "com.nytimes.android":                                                      "news",
    "com.oanda.currencyconverter":                                              "finance",
    "com.obreey.reader":                                                        "books",
    "com.officedocument.word.docx.document.viewer":                             "office",
    "com.olx.southasia":                                                        "shopping",
    "com.omgodse.notally":                                                      "notes",
    "com.onlineradio.fmradioplayer":                                            "music",
    "com.onlyoffice.documents":                                                 "office",
    "com.opentable":                                                            "food",
    "com.orangeannoe.englishdictionary":                                        "dictionary",
    "com.orbitz":                                                               "travel",
    "com.ovuline.pregnancy":                                                    "parenting",
    "com.oyo.consumer":                                                         "travel",
    "com.pal.train":                                                            "transit",
    "com.pantaloons":                                                           "shopping",
    "com.passporterapp.android":                                                "travel",
    "com.pcloud.pcloud":                                                        "cloud_storage",
    "com.pdffiller":                                                            "pdf",
    "com.peterengland.abfrl":                                                   "shopping",
    "com.phone.contact.call.phonecontact":                                      "contacts",
    "com.picsart.draw":                                                         "art",
    "com.picsart.studio":                                                       "photos",
    "com.pinterest":                                                            "social",
    "com.pirastudios.travelapp":                                                "travel",
    "com.plant.identify.care.app":                                              "plant",
    "com.plant.identify.plantcare.identifier":                                  "plant",
    "com.pocketbrilliance.reminders":                                           "reminders",
    "com.popularapp.thirtydayfitnesschallenge":                                 "fitness",
    "com.prestigia.androidApp":                                                 "travel",
    "com.prestigio.ereader":                                                    "books",
    "com.priceline.android.negotiator":                                         "travel",
    "com.produpress.immoweb":                                                   "real_estate",
    "com.project.vivareal":                                                     "real_estate",
    "com.puma.ecom.app":                                                        "shopping",
    "com.purplebookspvtltd.org":                                                "books",
    "com.qidian.Int.reader":                                                    "books",
    "com.qsstudio.clock.timer.theme":                                           "clock",
    "com.quikr":                                                                "shopping",
    "com.quip.quip":                                                            "productivity",
    "com.radio.fmradio":                                                        "music",
    "com.radiolight.etatsunis":                                                 "music",
    "com.radioline.android.radioline":                                          "music",
    "com.raha.app.mymoney.free":                                                "finance",
    "com.railyatri.in.mobile":                                                  "transit",
    "com.raincan.android.hybrid":                                               "grocery",
    "com.raising.prodigy":                                                      "parenting",
    "com.ranjith888999.onlinefurniturestore":                                   "shopping",
    "com.rarlab.rar":                                                           "files",
    "com.rccl.celebrity":                                                       "travel",
    "com.rccl.royalcaribbean":                                                  "travel",
    "com.rcrc.riyadhjourneyplanner":                                            "transit",
    "com.readly.client":                                                        "books",
    "com.readwhere.app":                                                        "books",
    "com.recime.app":                                                           "recipes",
    "com.recorder.voice.nonstop":                                               "recorder",
    "com.redbubble":                                                            "shopping",
    "com.redcrossfirstaidclone":                                                "health",
    "com.redfin.android":                                                       "real_estate",
    "com.rentalia.androidapp":                                                  "travel",
    "com.rentberry.android":                                                    "real_estate",
    "com.renthop.renthopconsumer":                                              "real_estate",
    "com.rgiskard.fairnote":                                                    "notes",
    "com.rhmsoft.code":                                                         "productivity",
    "com.riatech.cookbook":                                                     "recipes",
    "com.riatech.indianRecipesNew":                                             "recipes",
    "com.rightmove.android":                                                    "real_estate",
    "com.ril.ajio":                                                             "shopping",
    "com.ril.tira":                                                             "shopping",
    "com.roadtrippers":                                                         "travel",
    "com.rome2rio.www.rome2rio":                                                "transit",
    "com.roprop.fastcontacs":                                                   "contacts",
    "com.roqapps.mycurrencylite":                                               "finance",
    "com.rt.pinprickeffect.meditation":                                         "meditation",
    "com.rtistiq.app":                                                          "art",
    "com.rvappstudios.math.games.kids.addition.subtraction.multiplication.division": "education",
    "com.safeway.client.android.acme":                                          "grocery",
    "com.safeway.client.android.albertsons":                                    "grocery",
    "com.safeway.client.android.kings":                                         "grocery",
    "com.safeway.client.android.safeway":                                       "grocery",
    "com.samsung.ecomm.global.in":                                              "shopping",
    "com.savvytime.mobile":                                                     "clock",
    "com.scaleup.plantid":                                                      "plant",
    "com.scribd.app.reader0":                                                   "books",
    "com.sec.penup":                                                            "art",
    "com.sentryapplications.alarmclock":                                        "alarm_clock",
    "com.sfmta.mt.mobiletickets":                                               "transit",
    "com.shopclues":                                                            "shopping",
    "com.shopgate.android.app30712":                                            "shopping",
    "com.shopping.limeroad":                                                    "shopping",
    "com.shoppingdeal.sportsuncle":                                             "shopping",
    "com.sidechef.sidechef":                                                    "recipes",
    "com.sidechef.sidechef.partner.budgetbytes":                                "recipes",
    "com.sidelineswap.android":                                                 "shopping",
    "com.simplehabit.simplehabitapp":                                           "productivity",
    "com.simplemobiletools.clock":                                              "clock",
    "com.simplemobiletools.contacts":                                           "contacts",
    "com.simpler.dialer":                                                       "phone",
    "com.skype.raider":                                                         "video_call",
    "com.skypicker.main":                                                       "travel",
    "com.sleepmonitor.aio":                                                     "health",
    "com.smartwho.SmartAllCurrencyConverter":                                   "finance",
    "com.smilingmind.app":                                                      "meditation",
    "com.snapchat.android":                                                     "social",
    "com.snapdeal.main":                                                        "shopping",
    "com.socialnmobile.dictapps.notepad.color.note":                            "notes",
    "com.sociosoft.unzip":                                                      "files",
    "com.soundcloud.android":                                                   "music",
    "com.spinny.consumer":                                                      "automotive",
    "com.splendapps.adler":                                                     "notes",
    "com.splendapps.splendo":                                                   "todo",
    "com.splendapps.voicerec":                                                  "recorder",
    "com.splendapps.vox":                                                       "recorder",
    "com.sportsdirect.sdapp":                                                   "shopping",
    "com.spotify.music":                                                        "spotify",
    "com.spotlightsix.zentimerlite2":                                           "clock",
    "com.sslbeauty.storefront":                                                 "shopping",
    "com.sssports.sssports":                                                    "shopping",
    "com.stedor.instashop":                                                     "grocery",
    "com.stockx.stockx":                                                        "shopping",
    "com.storevn.weather":                                                      "weather",
    "com.strava":                                                               "fitness",
    "com.streema.simpleradio":                                                  "music",
    "com.stromming.planta":                                                     "plant",
    "com.subconscious.thrive":                                                  "health",
    "com.supercook.app":                                                        "recipes",
    "com.superelement.pomodoro":                                                "productivity",
    "com.surveyheart":                                                          "productivity",
    "com.surveymonkey":                                                         "productivity",
    "com.t11.skyviewfree":                                                      "education",
    "com.talabat":                                                              "food",
    "com.tatadigital.tcp":                                                      "shopping",
    "com.techniman.recipes.food":                                               "recipes",
    "com.tesco.grocery.view":                                                   "grocery",
    "com.thalys.thalys":                                                        "transit",
    "com.thetrainline":                                                         "transit",
    "com.thisisglobal.player.heart":                                            "music",
    "com.thomsonreuters.reuters":                                               "news",
    "com.threestar.gallery":                                                    "photos",
    "com.thriftbooks.mobile":                                                   "books",
    "com.ticktick.task":                                                        "todo",
    "com.todoist":                                                              "todo",
    "com.tohsoft.calculator":                                                   "calculator",
    "com.toi.reader.activities":                                                "news",
    "com.toys.online.shopping.apps.toysforkids.khilona.toy.toysshoppingapp":   "shopping",
    "com.toyspoint.app":                                                        "shopping",
    "com.tpml.dh":                                                              "news",
    "com.tranzmate":                                                            "transit",
    "com.traveloka.android":                                                    "travel",
    "com.treebo.starscream":                                                    "travel",
    "com.tripadvisor.tripadvisor":                                              "travel",
    "com.tripit":                                                               "travel",
    "com.tripomatic":                                                           "travel",
    "com.trivago":                                                              "travel",
    "com.trulia.android":                                                       "real_estate",
    "com.ttn.idanim":                                                           "meditation",
    "com.tul.tatacliq":                                                         "shopping",
    "com.twirsapps.weatherforecast":                                            "weather",
    "com.twitter.android":                                                      "social",
    "com.uairango":                                                             "travel",
    "com.ubermind.rei":                                                         "outdoor",
    "com.ubuy":                                                                 "shopping",
    "com.udemy.android":                                                        "education",
    "com.ukshopping.sonu":                                                      "shopping",
    "com.uniqlo.in.catalogue":                                                  "shopping",
    "com.upgrad.student":                                                       "education",
    "com.urbanic":                                                              "shopping",
    "com.urbanladder.catalog":                                                  "shopping",
    "com.urbanoutfitters.android":                                              "shopping",
    "com.usatoday.android.news":                                                "news",
    "com.valentino":                                                            "shopping",
    "com.versionupdate19359.frostweb":                                          "weather",
    "com.vimeo.android.videoapp":                                               "video",
    "com.vipulasri.artier":                                                     "art",
    "com.virtualkitchen":                                                       "recipes",
    "com.visitacity.visitacityapp":                                             "travel",
    "com.vitotechnology.sky.tonight.map.star.walk":                             "education",
    "com.vocab.app":                                                            "education",
    "com.vpn.basiccalculator":                                                  "calculator",
    "com.vsct.vsc.mobile.horaireetresa.android":                                "transit",
    "com.vuitton.android":                                                      "shopping",
    "com.wanderlog.android":                                                    "travel",
    "com.wanderu.wanderu":                                                      "transit",
    "com.washingtonpost.android":                                               "news",
    "com.waymarkedtrails.hiiker":                                               "outdoor",
    "com.waze":                                                                 "maps",
    "com.weather.Weather":                                                      "weather",
    "com.weather.forecast.channel.local":                                       "weather",
    "com.weather.forecast.weatherchannel":                                      "weather",
    "com.weather.free.forecast.dailyweather":                                   "weather",
    "com.weather.live.forecast.amwidget":                                       "weather",
    "com.weather.radar.forecast.now":                                           "weather",
    "com.weatherradar.liveradar.weathermap":                                    "weather",
    "com.weatherteam.rainy.forecast.radar.widgets":                             "weather",
    "com.weathervane.forecast.radar.android":                                   "weather",
    "com.wego.android":                                                         "travel",
    "com.westside":                                                             "shopping",
    "com.wetter.androidclient":                                                 "weather",
    "com.wggesucht.android":                                                    "real_estate",
    "com.whatsapp":                                                             "whatsapp",
    "com.winzip.android":                                                       "files",
    "com.wisdomlogix.meditation.music":                                         "meditation",
    "com.wise.converter":                                                       "finance",
    "com.withings.wiscale2":                                                    "fitness",
    "com.wondershare.pdfelement":                                               "pdf",
    "com.woodenstreet":                                                         "shopping",
    "com.woodland.offer.app":                                                   "shopping",
    "com.wordwebsoftware.android.wordweb":                                      "dictionary",
    "com.workout.fitness.exercise.loseweight.gymworkout":                       "fitness",
    "com.world.clock.smart.alarm.timer.stopwatch.tat":                          "clock",
    "com.worldtimeplanner.app":                                                 "clock",
    "com.wunderground.android.weather":                                         "weather",
    "com.xdev.docxreader.docx.docxviewer.document.doc.office.viewer.reader.word": "office",
    "com.xe.currency":                                                          "finance",
    "com.xiaomi.wearable":                                                      "fitness",
    "com.xodo.pdf.reader":                                                      "pdf",
    "com.yahoo.mobile.client.android.mail":                                     "email",
    "com.yahoo.mobile.client.android.weather":                                  "weather",
    "com.yatra.base":                                                           "travel",
    "com.yoox":                                                                 "shopping",
    "com.yummly.android":                                                       "recipes",
    "com.zabamobile.sportstimerfree":                                           "fitness",
    "com.zeptoconsumerapp":                                                     "grocery",
    "com.zgsinfotach.unitconverter":                                            "productivity",
    "com.zinio.mobile.android.reader":                                          "books",
    "com.zipoapps.voice.recorder.memos":                                        "recorder",
    "com.zoho.meeting":                                                         "video_call",
    "com.zoho.notebook":                                                        "notes",
    "com.zoho.show.app":                                                        "productivity",
    "com.zumobi.msnbc":                                                         "news",
    "consumer_app.mtvagl.com.marutivalue":                                      "automotive",
    "ctrip.english":                                                            "travel",
    "cz.worldeecom.worldee":                                                    "travel",
    "de.flixbus.app":                                                           "transit",
    "de.is24.android":                                                          "real_estate",
    "de.raumobil.android.busliniensuche":                                       "transit",
    "de.wetteronline.wetterapp":                                                "weather",
    "de.zalando.mobile":                                                        "shopping",
    "droom.sleepIfUCan":                                                        "alarm_clock",
    "easynotes.notes.notepad.notebook.privatenotes.note":                       "notes",
    "epub.reader":                                                              "books",
    "es.roid.and.trovit":                                                       "real_estate",
    "eu.baroncelli.oraritrenitalia":                                            "transit",
    "eu.coolblue.shop":                                                         "shopping",
    "eu.infobus.app":                                                           "transit",
    "evolly.plant.id.ai":                                                       "plant",
    "filemanager.files.fileexplorer":                                           "files",
    "fit.cure.android":                                                         "fitness",
    "fitapp.fittofit":                                                          "fitness",
    "fitness.online.app":                                                       "fitness",
    "fitnesscoach.workoutplanner.weightloss":                                   "fitness",
    "flipboard.app":                                                            "news",
    "fr.lekiosque":                                                             "books",
    "gallery.hidepictures.photovault.lockgallery":                              "photos",
    "group.gloo.sneakerthief":                                                  "shopping",
    "gymworkout.gym.gymlog.gymtrainer":                                         "fitness",
    "hdesign.theclock":                                                         "clock",
    "homeworkout.fitness.app":                                                  "fitness",
    "homeworkout.homeworkouts.noequipment":                                     "fitness",
    "in.amazon.mShop.android.shopping":                                         "shopping",
    "in.dmart":                                                                 "grocery",
    "in.evolve.android":                                                        "fitness",
    "in.goindigo.android":                                                      "travel",
    "in.hamleys.www":                                                           "shopping",
    "in.mylo.pregnancy.baby.app":                                               "parenting",
    "in.smsoft.justremind":                                                     "reminders",
    "in.winni.app":                                                             "shopping",
    "inc.techxonia.icemedicalreportsemergency":                                 "health",
    "industrybuying.com.industrybuying":                                        "shopping",
    "info.mta.mymta":                                                           "transit",
    "io.lambus.app":                                                            "travel",
    "io.makeroid.bsdeora55520.usa":                                             "music",
    "it.immobiliare.android":                                                   "real_estate",
    "it.italotreno":                                                            "transit",
    "je.fit":                                                                   "fitness",
    "jp.gocro.smartnews.android":                                               "news",
    "jp.randyapps.timedifferenceclock":                                         "clock",
    "ktech.sketchar":                                                           "art",
    "meditofoundation.medito":                                                  "meditation",
    "mega.privacy.android.app":                                                 "cloud_storage",
    "mobi.drupe.app":                                                           "contacts",
    "myrecorder.voicerecorder.voicememos.audiorecorder.recordingapp":           "recorder",
    "net.artsy.app":                                                            "art",
    "net.skyscanner.android.main":                                              "travel",
    "net.xelnaga.exchanger":                                                    "finance",
    "nithra.offline.personal.official.letter.templates":                        "productivity",
    "nl.gvb.reizigersapp":                                                      "transit",
    "note.notesapp.notebook.notepad.stickynotes.colornote":                     "notes",
    "notes.notepad.checklist.calendar.todolist.notebook":                       "notes",
    "notion.id":                                                                "notes",
    "nz.co.impressioncreative.timezone_viewer":                                 "clock",
    "omegacentauri.mobi.simplestopwatch":                                       "clock",
    "org.coursera.android":                                                     "education",
    "org.edx.mobile":                                                           "education",
    "org.eurail.railplanner":                                                   "transit",
    "org.heartfulness.heartintune.prod":                                        "meditation",
    "org.iggymedia.periodtracker":                                              "health",
    "org.khanacademy.android":                                                  "education",
    "org.oppia.android":                                                        "education",
    "org.plantnet":                                                             "plant",
    "org.railstotrails.traillink":                                              "outdoor",
    "org.readera":                                                              "books",
    "org.un.mobile.news":                                                       "news",
    "org.wakingup.android":                                                     "meditation",
    "org.whiteglow.quickeycalculator":                                          "calculator",
    "pedometer.steptracker.calorieburner.stepcounter":                          "fitness",
    "pl.patraa.timezoneconverter":                                              "clock",
    "pl.solidexplorer2":                                                        "files",
    "plant.identification.flower.tree.leaf.identifier.identify.cat.dog.breed.nature": "plant",
    "plant.identification.snap":                                                "plant",
    "ru.yandex.yandexmaps":                                                     "maps",
    "ru.zdevs.zarchiver":                                                       "files",
    "se.lichtenstein.mind.en":                                                  "meditation",
    "shoppersstop.shoppersstop":                                                "shopping",
    "sixpack.sixpackabs.absworkout":                                            "fitness",
    "softmaker.applications.office.presentations":                              "office",
    "steptracker.healthandfitness.walkingtracker.pedometer":                    "fitness",
    "todolist.scheduleplanner.dailyplanner.todo.reminders":                     "todo",
    "trendyol.com":                                                             "shopping",
    "tunein.player":                                                            "music",
    "uk.co.bbc.goodfood2":                                                      "recipes",
    "uk.co.serenity.guided.meditation":                                         "meditation",
    "us.zoom.videomeetings":                                                    "zoom",
    "voicerecorder.audiorecorder.voice":                                        "recorder",
    "vr.audio.voicerecorder":                                                   "recorder",
    "wp.wattpad":                                                               "books",
    "yallatoys.com.cherry":                                                     "shopping",
}
import re as _re_norm
# ── Packages to DROP entirely (no records imported) ─────────────────────────
# System internals, moon-phase apps, single-vendor tools with no transferable
# interaction patterns, unidentifiable packages.
_DROP_PACKAGES: Set[str] = {
    "android",                                    # Android OS itself
    "app.cybrook.teamlink",                       # reclassified above — actually keep
    "com.android.intentresolver",                 # system dialog
    "com.android.printspooler",                   # system
    "com.google.android.gms",                     # Play Services — system
    "com.google.android.packageinstaller",        # system
    "com.google.android.permissioncontroller",    # system
    "com.google.android.providers.media.module",  # system
    "com.google.android.settings.intelligence",   # system
    "com.heytap.market",                          # OEM-specific store
    "by.olion.Moon",                              # moon phase — no transferable UX
    "com.dafftin.android.moon_phase",
    "com.jrustonapps.mymoonphase",
    "com.moonly.android",
    "com.probadosoft.moonphasecalendar",
    "com.rareworksllc.android.lunarphase",
    "com.universetoday.moon.free",
    "co.tapcart.app.id_5jxS78iFmF",              # generic Tapcart shells
    "co.tapcart.app.id_DQtUWFFKKx",
    "co.tapcart.app.id_aa3hjpTC3q",
    "co.tapcart.app.id_c6aPCt7GIc",
    "com.SendGroupSMS.PaperArtOrigamiExpert",     # origami — no useful UX steps
    "com.mobilicos.howtomakeorigami",
    "com.saphira.art.origami",
    "com.agbe.artize",                            # unidentifiable niche
    "com.agbe.jaquar",
    "com.app.disposeit",
    "com.app.khelmarta",
    "com.apkpure.aegon",                          # third-party app store
    "com.astrapaging.cm",                         # system-level
    "com.aws.android",                            # developer tool
    "com.bash.prod",                              # unidentifiable
    "com.bigbfs.app",
    "com.candl.athena",
    "com.czaam",
    "com.fenchtose.reflog",
    "com.hardwareshackapp2",
    "com.indra.haramain.pro",
    "com.ingeniooz.hercule",
    "com.jd.mca",
    "com.jollee",
    "com.jollybration",
    "com.just.direct",
    "com.lightlink.tileswaleApp",
    "com.maritimeoptima.production",
    "com.megahardware",
    "com.optimalsolutionjo.dokanti",
    "com.pavelkozemirov.guesstheartist",
    "com.peggy.mobile",
    "com.phdv.universal",
    "com.phl.topmosthardware.vendor",
    "com.pickery.app",
    "com.qamar.ide.web",
    "com.radical.infotech.warehouse",
    "com.rapidbox",
    "com.rstream.crafts",
    "com.sanitarybazaar.sb",
    "com.sar.app",
    "com.seacloud.bc",
    "com.sortizy",
    "com.splendapp.rasport",
    "com.svtindia.tulsiproducts",
    "com.wilson.live",
    "com.wte.view",
    "consumer_app.mtvagl.com.marutivalue",        # reclassified above — keep
    "dsgui.android",
    "fc.admin.fcexpressadmin",
    "greg.io.care_app_android",
    "in.achivr",
    "in.vijetha.live",
    "io.ma.ma_mobile",
    "io.spck",
    "space.trs",
    "uk.co.icectoc.customer",
    "unknown",                                    # literal "unknown" package
    "yo.app.free",
}


def _normalize_app(app_raw: str) -> str:
    """
    Convert a raw package ID or short name to a canonical category string.

    Resolution order:
      1. Exact match in _PKG_CATEGORY_MAP
      2. Substring match (handles versioned or prefixed packages)
      3. Already a short name (no dots) → return as-is
      4. Fallback: last segment of package
    """
    if not app_raw:
        return "unknown"

    raw = app_raw.strip()

    # Exact match
    if raw in _PKG_CATEGORY_MAP:
        return _PKG_CATEGORY_MAP[raw]

    # Case-insensitive exact match (handles capitalised packages like com.Dominos)
    lower = raw.lower()
    for pkg, cat in _PKG_CATEGORY_MAP.items():
        if pkg.lower() == lower:
            return cat

    # Substring match — e.g. "com.google.android.deskclock" → "clock"
    for pkg, cat in _PKG_CATEGORY_MAP.items():
        if pkg in raw or raw in pkg:
            return cat

    # Already a short human-readable name (no dots) → pass through unchanged
    if "." not in raw:
        return raw.lower()

    # Last segment fallback
    return raw.rsplit(".", 1)[-1].lower()


def should_drop(app_raw: str) -> bool:
    """Return True if records for this package should be excluded from the DB."""
    if not app_raw:
        return True
    raw = app_raw.strip()
    if raw in _DROP_PACKAGES:
        return True

    # If package is explicitly mapped to a canonical category,
    # treat it as user-facing and keep it (even if it matches broad prefixes).
    if raw in _PKG_CATEGORY_MAP:
        return False
    raw_lower = raw.lower()
    if any(pkg.lower() == raw_lower for pkg in _PKG_CATEGORY_MAP):
        return False

    # Drop system-looking packages not explicitly listed
    drop_prefixes = (
        "com.android.",
        "com.google.android.gms",
        "com.google.android.packageinstaller",
        "com.google.android.permissioncontroller",
        "com.google.android.providers",
        "com.google.android.settings.intelligence",
    )
    return any(raw.startswith(p) for p in drop_prefixes)

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
_DIR         = os.path.dirname(os.path.abspath(__file__))
CHROMA_PATH  = os.path.join(_DIR, "task_memory_db")
JSONL_PATH   = os.path.join(_DIR, "..", "..", "..", "..", "Android-Dataset", "cleaned_recipes.jsonl")

# ── Retrieval thresholds ───────────────────────────────────────────────────
THRESHOLD_EXECUTE  = 0.85   # ≥ this → run as script
THRESHOLD_HINT     = 0.65   # ≥ this → inject as hint
SIGNATURE_OVERLAP  = 0.60   # minimum Jaccard for screen signature match
FUZZY_TEXT_THRESH  = 0.80   # minimum SequenceMatcher ratio for fuzzy element text

# ── Embedding model ────────────────────────────────────────────────────────
# BAAI/bge-small-en-v1.5: best MTEB retrieval score in this size class (~30 MB)
# Outperforms all-MiniLM-L6-v2 on semantic similarity tasks while being same size.
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

# How many entries from each category to load during bulk import
MAX_IMPORT_PER_APP = 500    # cap per app to avoid one app dominating
IMPORT_BATCH_SIZE  = 128    # ChromaDB batch insert size


# ═══════════════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RecipeStep:
    """One atomic step retrieved from the golden paths collection."""
    record_id:        str
    step_instruction: str
    overall_goal:     str
    app:              str
    action_type:      str
    screen_signature: str
    selectors:        List[Dict[str, str]]   # priority-ordered
    param_key:        Optional[str]
    direction:        Optional[str]
    typed_value:      Optional[str]
    expect_screen_change: bool
    similarity:       float
    success_count:    int
    failure_count:    int = 0


@dataclass
class RetrievalResult:
    """
    What `TaskMemory.query()` returns.
    The caller uses `band` to decide how to use the results.
    """
    band:      str            # "execute" | "hint" | "none"
    recipes:   List[RecipeStep] = field(default_factory=list)
    hint_text: str = ""
    best_sim:   float = 0.0
    best_label: str   = ""


# ═══════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_step_for_overlap(text: str) -> str:
    """
    Replace volatile numeric and email values with placeholders for overlap calculation.
    Treats "Set hours to 7" and "Set hours to 3" as identical after normalization.
    Only used for keyword overlap; does not affect stored text or embedding.
    """
    text = _re_norm.sub(r'\b\d+(?::\d+)?(?:\s*(?:am|pm))?\b', 'NUM', text, flags=_re_norm.IGNORECASE)
    text = _re_norm.sub(r'\b[\w.%+-]+@[\w.-]+\.[a-zA-Z]{2,}\b', 'EMAIL', text)
    return text

def _normalize_goal_for_embedding(goal: str) -> str:
    """
    Strip volatile values that vary between instances of the same task class.
    This keeps equivalent goals closer in embedding space.

    NOTE: only used for embedding; original goal text remains unchanged for LLM prompts.
    """
    # Email addresses
    goal = _re_norm.sub(
        r'\b[\w.+%-]{2,}@[\w.-]+\.[a-zA-Z]{2,}\b',
        'EMAIL_ADDR',
        goal,
    )
    # Times like 7:00 AM / 4:35 am / 10:30
    goal = _re_norm.sub(
        r'\b\d{1,2}:\d{2}\s*(?:am|pm)\b',
        'TIME_VALUE',
        goal,
        flags=_re_norm.IGNORECASE,
    )
    goal = _re_norm.sub(r'\b\d{1,2}:\d{2}\b', 'TIME_VALUE', goal)
    # Standalone hour + period
    goal = _re_norm.sub(r'\b\d{1,2}\s*(?:AM|PM|am|pm)\b', 'TIME_VALUE', goal)
    # URLs
    goal = _re_norm.sub(r'https?://\S+', 'URL', goal)
    # Phone numbers
    goal = _re_norm.sub(r'\b\d{7,}\b', 'PHONE_NUM', goal)
    return goal.strip()

def _build_composite_document(overall_goal: str, step_instruction: str) -> str:
    """
    Produces the string that gets embedded.
    Both goal and step are normalized to remove volatile values (numbers, emails)
    before embedding so that identical action steps can match across task-specific
    parameters. E.g., "Set hours to 7" and "Set hours to 3" become identical.
    
    NOTE: This normalization only affects embedding. Original text is preserved
    for selector resolution and LLM prompts.
    """
    g = _normalize_goal_for_embedding((overall_goal or "").strip())
    s = _normalize_step_for_overlap((step_instruction or "").strip())
    if g:
        # Step instruction twice (normalized), goal once (normalized) — step is what we match on
        return f"{s} [SEP] {s} [SEP] {g}"
    return s


def _signature_jaccard(sig_a: str, sig_b: str) -> float:
    """
    Compute Jaccard overlap between two screen signatures.
    Signatures are comma-separated 'ClassName:resource_id_tail' tokens.
    Returns 0.0–1.0. Returns 1.0 if both are empty.
    """
    if not sig_a and not sig_b:
        return 1.0
    set_a = set(sig_a.split(",")) - {""}
    set_b = set(sig_b.split(",")) - {""}
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    inter = set_a & set_b
    return len(inter) / len(union)


def _fuzzy_text_match(a: str, b: str) -> float:
    """SequenceMatcher ratio between two strings, both lowercased."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# ═══════════════════════════════════════════════════════════════════════════
#  ELEMENT RESOLVER
# ═══════════════════════════════════════════════════════════════════════════

def resolve_element(
    selectors: List[Dict[str, str]],
    elements: list,
    blacklist: Optional[Set[int]] = None,
    params: Optional[Dict[str, str]] = None,
) -> Optional[int]:
    """
    Try each selector in priority order against the live accessibility tree.
    Priority: resource_id → resource_id_tail → content_desc → text → class_name

    For text-based selectors, falls back to fuzzy matching (Levenshtein)
    if exact match fails but similarity >= FUZZY_TEXT_THRESH.

    Returns the element_id of the best match, or None.

    [CACHE] resolution attempts are logged at DEBUG level.
    """
    blacklist = blacklist or set()
    params    = params    or {}

    def _attr(e, key: str) -> str:
        v = e.get(key) if isinstance(e, dict) else getattr(e, key, None)
        return (v or "").strip()

    def _get_id(e) -> int:
        return e.get("element_id", -1) if isinstance(e, dict) else getattr(e, "element_id", -1)

    def _interpolate(template: str) -> str:
        for k, v in params.items():
            template = template.replace("{" + k + "}", str(v))
        return template

    for sel in selectors:
        by    = sel.get("by", "")
        value = _interpolate(sel.get("value", ""))

        exact_match  = None
        fuzzy_match  = None
        fuzzy_score  = 0.0

        for elem in elements:
            eid = _get_id(elem)
            if eid in blacklist:
                continue

            if by == "resource_id":
                rid = _attr(elem, "resource_id")
                if rid.lower() == value.lower():
                    exact_match = eid
                    break

            elif by == "resource_id_tail":
                rid = _attr(elem, "resource_id")
                tail = rid.rsplit("/", 1)[-1] if "/" in rid else rid
                if tail.lower() == value.lower():
                    exact_match = eid
                    break

            elif by == "content_desc":
                desc = _attr(elem, "content_description")
                if desc.lower() == value.lower():
                    exact_match = eid
                    break
                score = _fuzzy_text_match(desc, value)
                if score > fuzzy_score:
                    fuzzy_score = score
                    fuzzy_match = eid

            elif by == "text":
                text = _attr(elem, "text")
                if text.lower() == value.lower():
                    exact_match = eid
                    break
                score = _fuzzy_text_match(text, value)
                if score > fuzzy_score:
                    fuzzy_score = score
                    fuzzy_match = eid

            elif by == "hint_text":
                hint = _attr(elem, "hint_text")
                if hint.lower() == value.lower():
                    exact_match = eid
                    break
                score = _fuzzy_text_match(hint, value)
                if score > fuzzy_score:
                    fuzzy_score = score
                    fuzzy_match = eid

            elif by == "class_name":
                cls = _attr(elem, "class_name") or _attr(elem, "type")
                tail = cls.rsplit(".", 1)[-1] if "." in cls else cls
                if tail.lower() == value.lower():
                    exact_match = eid
                    break

        if exact_match is not None:
            logger.debug(
                f"[CACHE] resolve_element: exact match by={by} value='{value}' → elem {exact_match}"
            )
            return exact_match

        if fuzzy_match is not None and fuzzy_score >= FUZZY_TEXT_THRESH:
            logger.debug(
                f"[CACHE] resolve_element: fuzzy match by={by} value='{value}' "
                f"score={fuzzy_score:.2f} → elem {fuzzy_match}"
            )
            return fuzzy_match

        logger.debug(
            f"[CACHE] resolve_element: no match by={by} value='{value}' "
            f"best_fuzzy={fuzzy_score:.2f}"
        )

    return None


# ═══════════════════════════════════════════════════════════════════════════
#  TASK MEMORY
# ═══════════════════════════════════════════════════════════════════════════
import re as _re

def _keyword_rerank(
    self,
    query_step: str,
    recipes: List[RecipeStep],
    query_goal: str = "",
    query_app: Optional[str] = None,
) -> List[RecipeStep]:
    """
    Rerank by blending three components:
      1. raw cosine similarity (0.6 weight)
      2. step-level keyword overlap, parameter-normalized (0.2 weight)
      3. goal-level keyword overlap, parameter-normalized (0.2 weight)
    
    Parameter-normalized means numbers and emails are replaced with placeholders
    for overlap calculation only; stored steps are not modified.
    """
    import re as _re
    STOPWORDS = {
        'the', 'a', 'an', 'to', 'on', 'in', 'of', 'at', 'for', 'and', 'or',
        'with', 'by', 'is', 'it', 'please', 'now', 'then', 'next',
    }
    
    # Normalize query step and goal for overlap calculation
    q_step_norm = _normalize_step_for_overlap(query_step)
    q_goal_norm = _normalize_step_for_overlap(query_goal) if query_goal else ""
    
    # Extract keywords from normalized query
    q_words_step = set(_re.findall(r'\b\w+\b', q_step_norm.lower())) - STOPWORDS
    q_words_goal = set(_re.findall(r'\b\w+\b', q_goal_norm.lower())) - STOPWORDS if q_goal_norm else set()
    q_symbols = set(_re.findall(r'[+#@&]', query_step))
    q_words_step.update(q_symbols)
    
    reranked = []
    for r in recipes:
        # Normalize stored step and goal
        step_norm = _normalize_step_for_overlap(r.step_instruction)
        goal_norm = _normalize_step_for_overlap(r.overall_goal)
        
        # Extract keywords from normalized stored step and goal
        s_words_step = set(_re.findall(r'\b\w+\b', step_norm.lower())) - STOPWORDS
        s_words_goal = set(_re.findall(r'\b\w+\b', goal_norm.lower())) - STOPWORDS
        s_symbols = set(_re.findall(r'[+#@&]', r.step_instruction))
        s_words_step.update(s_symbols)
        
        # Compute overlaps
        step_overlap = len(q_words_step & s_words_step) / max(len(q_words_step), 1)
        goal_overlap = len(q_words_goal & s_words_goal) / max(len(q_words_goal), 1) if q_words_goal else 0.0
        
        # Blend: 0.6 raw + 0.3 step_overlap + 0.1 goal_overlap
        blended = r.similarity * 0.6 + step_overlap * 0.3 + goal_overlap * 0.1

        # Goal-context penalty for dangerous wrong-purpose matches.
        if (
            len(q_words_goal) >= 3
            and goal_overlap < 0.15
            and r.similarity > 0.80
        ):
            blended = blended * 0.65

        if query_app and r.app and r.app.lower() != query_app.lower() and r.similarity < 0.95:
            blended = blended * 0.70
        
        # Hard penalty only for semantically weak zero-overlap matches
        if step_overlap == 0 and len(q_words_step) >= 2 and r.similarity < 0.82:
            blended = min(blended, 0.65)
        
        ucb = self._ucb_reliability(r)
        blended_adjusted = blended * ucb
        reranked.append(dataclasses.replace(r, similarity=blended_adjusted))
    return sorted(reranked, key=lambda x: -x.similarity)

class TaskMemory:
    """
    ChromaDB-backed semantic memory for atomic task steps.

    Usage:
        mem = TaskMemory()
        result = mem.query(
            step_instruction="Compose new email to hayadawy@icloud.com",
            overall_goal="Send rescheduling email",
            app="gmail",
            current_signature="ImageButton:compose_button,EditText:to,...",
        )
        if result.band == "execute":
            # step-by-step execution with element verification
            for step in result.recipes:
                eid = resolve_element(step.selectors, live_elements)
                ...
        elif result.band == "hint":
            llm_context += result.hint_text
    """

    COLLECTION = "golden_paths"

    def __init__(
        self,
        chroma_path: str = CHROMA_PATH,
        jsonl_path:  str = JSONL_PATH,
    ):
        self._chroma_path = chroma_path
        self._jsonl_path  = jsonl_path
        self._embedder    = None   # lazy-loaded

        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=chroma_path)
            self._col    = self._client.get_or_create_collection(
                name     = self.COLLECTION,
                metadata = {"hnsw:space": "cosine"},
            )
            logger.info(
                f"[CACHE] TaskMemory init: {self._col.count()} records in {chroma_path}"
            )
        except Exception as e:
            logger.error(f"[CACHE] ChromaDB init failed: {e}")
            self._client = None
            self._col    = None

        # One-time migration: remove preloaded dataset and keep only agent-learned cache.
        self._maybe_clear_preloaded_dataset()

    def _maybe_clear_preloaded_dataset(self) -> None:
        """One-time migration: delete preloaded demonstrated records, keep agent-learned entries."""
        if self._col is None:
            return

        migration_flag = os.path.join(self._chroma_path, ".agent_only_cache_v1")
        if os.path.exists(migration_flag):
            return

        try:
            probe = self._col.get(where={"demonstrated": {"$eq": 1}}, limit=1)
            has_preloaded = bool((probe or {}).get("ids"))

            if has_preloaded:
                logger.info("[CACHE] Clearing pre-loaded dataset — switching to agent-only cache")
                records = self._col.get(where={"demonstrated": {"$eq": 1}})
                ids = (records or {}).get("ids") or []
                if ids:
                    self._col.delete(ids=ids)
                    logger.info(f"[CACHE] Cleared {len(ids)} pre-loaded records")

            with open(migration_flag, "w", encoding="utf-8") as f:
                f.write("ok\n")
        except Exception as e:
            logger.warning(f"[CACHE] Migration check failed: {e}")

    # ── Embedding ──────────────────────────────────────────────────────────

    def _get_embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            logger.info(f"[CACHE] Loading embedding model {EMBED_MODEL} …")
            self._embedder = SentenceTransformer(EMBED_MODEL)
            logger.info(f"[CACHE] Embedding model ready")
        return self._embedder

    def _embed(self, texts: List[str]) -> List[List[float]]:
        model = self._get_embedder()
        return model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

    def _keyword_rerank(
        self,
        query_step: str,
        recipes: List[RecipeStep],
        query_goal: str = "",
        query_app: Optional[str] = None,
    ) -> List[RecipeStep]:
        """Re-rank retrieved recipes by keyword overlap + semantic similarity + goal alignment."""
        return _keyword_rerank(self, query_step, recipes, query_goal, query_app)

    def _ucb_reliability(self, record: RecipeStep) -> float:
        """
        Beta-posterior style reliability multiplier for retrieval confidence.
        Returns multiplier in [0.6, 1.15].
        """
        s = int(getattr(record, "success_count", 0) or 0)
        f = int(getattr(record, "failure_count", 0) or 0)
        n = s + f
        if n == 0:
            return 1.0
        reliability = (s + 1) / (n + 2)
        return 0.6 + 0.55 * reliability

    # ── Dataset loading ────────────────────────────────────────────────────

    def _load_from_jsonl(self, path: str):
        if not os.path.exists(path):
            logger.warning(f"[CACHE] JSONL not found at {path} — starting with empty collection")
            return

        logger.info(f"[CACHE] Populating ChromaDB from {path} …")
        start = time.time()

        per_app: Dict[str, List[dict]] = {}
        dropped = 0
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                app_raw = rec.get("app", "unknown")
                if should_drop(app_raw):
                    dropped += 1
                    continue
                # Normalize to short canonical name BEFORE storing
                rec["app"] = _normalize_app(app_raw)
                per_app.setdefault(rec["app"], []).append(rec)
        logger.info(f"[CACHE] Dropped {dropped} records from excluded packages")

        # Cap per app to avoid any one app dominating
        records: List[dict] = []
        for app, recs in per_app.items():
            records.extend(recs[:MAX_IMPORT_PER_APP])

        logger.info(
            f"[CACHE] {len(records)} steps selected across {len(per_app)} apps"
        )

        # Insert in batches
        inserted = 0
        for start_idx in range(0, len(records), IMPORT_BATCH_SIZE):
            batch = records[start_idx : start_idx + IMPORT_BATCH_SIZE]

            documents  = []
            metadatas  = []
            ids        = []

            for rec in batch:
                doc = _build_composite_document(
                    rec.get("overall_goal", ""),
                    rec.get("step_instruction", ""),
                )
                documents.append(doc)
                ids.append(str(uuid.uuid4()))
                metadatas.append({
                    "step_instruction": (rec.get("step_instruction") or "")[:300],
                    "overall_goal":     (rec.get("overall_goal") or "")[:300],
                    "app":              rec.get("app") or "unknown",
                    "action_type":      rec.get("action_type") or "click",
                    "screen_signature": (rec.get("screen_signature") or "")[:500],
                    "selectors":        json.dumps(rec.get("selectors") or []),
                    "param_key":        rec.get("param_key") or "",
                    "direction":        rec.get("direction") or "",
                    "typed_value":      rec.get("typed_value") or "",
                    "expect_screen_change": str(rec.get("expect_screen_change", True)),
                    "success_count":    1,
                    "failure_count":    0,
                    "demonstrated":     0,
                })

            embeddings = self._embed(documents)

            try:
                self._col.add(
                    ids        = ids,
                    documents  = documents,
                    embeddings = embeddings,
                    metadatas  = metadatas,
                )
                inserted += len(batch)
            except Exception as e:
                logger.warning(f"[CACHE] Batch insert error: {e}")

        elapsed = time.time() - start
        logger.info(
            f"[CACHE] Dataset loaded: {inserted} steps in {elapsed:.1f}s  "
            f"| collection size: {self._col.count()}"
        )

    # ── Query ──────────────────────────────────────────────────────────────

    def query(
        self,
        step_instruction:  str,
        overall_goal:      str,
        app:               str,
        current_signature: str = "",
        top_k:             int = 5,
    ) -> RetrievalResult:
        """
        Find the most semantically similar stored steps for this task.

        Returns a RetrievalResult with band "execute", "hint", or "none".

        [CACHE] The full retrieval trace is logged at DEBUG level.
        """
        if self._col is None:
            logger.debug("[CACHE] query: collection unavailable → band=none")
            return RetrievalResult(band="none")

        if self._col.count() == 0:
            logger.debug("[CACHE] query: collection empty → band=none")
            return RetrievalResult(band="none")

        doc = _build_composite_document(overall_goal, step_instruction)
        logger.debug(f"[CACHE] query: '{step_instruction[:60]}' | app={app}")

        # Try app-filtered query first
        recipes = self._do_query(doc, app=app, top_k=top_k)

        # Fall back to unfiltered if no results for this app
        if not recipes:
            logger.debug(f"[CACHE] query: no results for app={app}, trying unfiltered")
            recipes = self._do_query(doc, app=None, top_k=top_k)

        # Re-rank by keyword overlap to prevent topically-similar but
        # action-dissimilar hits from staying at the top.
        if recipes:
            recipes = self._keyword_rerank(step_instruction, recipes, overall_goal, app)

        if not recipes:
            logger.debug("[CACHE] query: no results at all → band=none")
            return RetrievalResult(band="none")

        # Keep top raw retrieval before any downstream filtering
        raw_best = recipes[0] if recipes else None
        raw_best_sim = raw_best.similarity if raw_best else 0.0
        raw_best_label = raw_best.step_instruction if raw_best else ""

        # Apply signature filtering — remove steps where screen looks very different
        if current_signature:
            before = len(recipes)
            recipes = [
                r for r in recipes
                if _signature_jaccard(r.screen_signature, current_signature) >= SIGNATURE_OVERLAP
                   or not r.screen_signature  # keep steps with no signature (scroll, back, etc.)
            ]
            logger.debug(
                f"[CACHE] signature filter: {before} → {len(recipes)} "
                f"(threshold={SIGNATURE_OVERLAP:.0%})"
            )

        if not recipes:
            logger.debug("[CACHE] query: all results filtered by signature → band=none")
            return RetrievalResult(
                band="none",
                best_sim=raw_best_sim,
                best_label=raw_best_label,
            )

        best = recipes[0]
        logger.info(
            f"[CACHE] best match: sim={best.similarity:.3f}  "
            f"app={best.app}  "
            f"'{best.step_instruction[:55]}'"
        )

        if best.similarity >= THRESHOLD_EXECUTE:
            logger.info(f"[CACHE] band=EXECUTE (sim={best.similarity:.3f} ≥ {THRESHOLD_EXECUTE})")
            return RetrievalResult(
                band="execute",
                recipes=recipes,
                best_sim=raw_best_sim,
                best_label=raw_best_label,
            )

        if best.similarity >= THRESHOLD_HINT:
            hint = self._build_hint(recipes, step_instruction)
            logger.info(
                f"[CACHE] band=HINT (sim={best.similarity:.3f} ≥ {THRESHOLD_HINT})  "
                f"hint_len={len(hint)}"
            )
            return RetrievalResult(
                band="hint",
                recipes=recipes,
                hint_text=hint,
                best_sim=raw_best_sim,
                best_label=raw_best_label,
            )

        logger.debug(f"[CACHE] band=NONE (best sim={best.similarity:.3f} < {THRESHOLD_HINT})")
        return RetrievalResult(
            band="none",
            best_sim=raw_best_sim,
            best_label=raw_best_label,
        )

    def _do_query(
        self,
        document:   str,
        app:        Optional[str],
        top_k:      int,
    ) -> List[RecipeStep]:
        """Execute a ChromaDB vector query and parse results."""
        try:
            n = min(top_k, self._col.count())
            if n == 0:
                return []

            # CRITICAL: embed query with same model used for indexed vectors
            query_embedding = self._embed([document])

            kwargs: Dict[str, Any] = {
                "query_embeddings": query_embedding,
                "n_results":        n,
                "include":          ["metadatas", "distances", "documents"],
            }
            if app:
                kwargs["where"] = {"app": app.lower()}

            results = self._col.query(**kwargs)
        except Exception as e:
            logger.warning(f"[CACHE] ChromaDB query error: {e}")
            return []

        recipes: List[RecipeStep] = []
        ids       = results.get("ids",       [[]])[0]
        metas     = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for rid, meta, dist in zip(ids, metas, distances):
            similarity = max(0.0, min(1.0, 1.0 - float(dist)))
            try:
                selectors = json.loads(meta.get("selectors") or "[]")
            except (json.JSONDecodeError, TypeError):
                selectors = []

            recipes.append(RecipeStep(
                record_id        = rid,
                step_instruction = meta.get("step_instruction", ""),
                overall_goal     = meta.get("overall_goal", ""),
                app              = meta.get("app", ""),
                action_type      = meta.get("action_type", "click"),
                screen_signature = meta.get("screen_signature", ""),
                selectors        = selectors,
                param_key        = meta.get("param_key") or None,
                direction        = meta.get("direction") or None,
                typed_value      = meta.get("typed_value") or None,
                expect_screen_change = meta.get("expect_screen_change", "True") == "True",
                similarity       = similarity,
                success_count    = int(meta.get("success_count", 1)),
                failure_count    = int(meta.get("failure_count", 0) or 0),
            ))

        return recipes

    # ── Store ──────────────────────────────────────────────────────────────

    def store(
        self,
        step_instruction:    str,
        overall_goal:        str,
        app:                 str,
        action_type:         str,
        screen_signature:    str,
        selectors:           List[Dict[str, str]],
        param_key:           Optional[str]  = None,
        direction:           Optional[str]  = None,
        typed_value:         Optional[str]  = None,
        expect_screen_change: bool          = True,
        demonstrated:        int            = 0,
        success_count:       int            = 1,
        failure_count:       int            = 0,
    ) -> Optional[str]:
        """
        Store one step into ChromaDB.
        Returns the assigned record_id, or None on failure.

        [CACHE] Store events are logged at INFO level.
        """
        if self._col is None:
            return None

        record_id = str(uuid.uuid4())
        doc = _build_composite_document(overall_goal, step_instruction)

        try:
            embedding = self._embed([doc])
            self._col.add(
                ids        = [record_id],
                documents  = [doc],
                embeddings = embedding,
                metadatas  = [{
                    "step_instruction": (step_instruction or "")[:300],
                    "overall_goal":     (overall_goal or "")[:300],
                    "app":              (app or "unknown").lower(),
                    "action_type":      action_type,
                    "screen_signature": (screen_signature or "")[:500],
                    "selectors":        json.dumps(selectors),
                    "param_key":        param_key   or "",
                    "direction":        direction   or "",
                    "typed_value":      typed_value or "",
                    "expect_screen_change": str(expect_screen_change),
                    "success_count":    success_count,
                    "failure_count":    failure_count,
                    "demonstrated":     demonstrated,
                }],
            )
            logger.info(
                f"[CACHE] stored step: '{step_instruction[:55]}'  "
                f"app={app}  action={action_type}  id={record_id[:8]}"
            )
            return record_id
        except Exception as e:
            logger.warning(f"[CACHE] store failed: {e}")
            return None

    def increment_success(self, record_id: str):
        """Bump success_count on an existing record after confirmed success."""
        if self._col is None:
            return
        try:
            existing = self._col.get(ids=[record_id], include=["metadatas"])
            if not existing["ids"]:
                return
            meta = dict(existing["metadatas"][0])
            meta["success_count"] = int(meta.get("success_count", 1)) + 1
            self._col.update(ids=[record_id], metadatas=[meta])
            logger.debug(
                f"[CACHE] increment_success id={record_id[:8]} "
                f"→ count={meta['success_count']}"
            )
        except Exception as e:
            logger.debug(f"[CACHE] increment_success failed: {e}")

    def mark_failure(self, record_id: str) -> None:
        """Increment failure_count for a cached step that led to a bad outcome."""
        if not record_id or self._col is None:
            return
        try:
            existing = self._col.get(ids=[record_id], include=["metadatas"])
            if not existing.get("ids"):
                return
            meta = dict(existing["metadatas"][0])
            meta["failure_count"] = int(meta.get("failure_count", 0) or 0) + 1
            self._col.update(ids=[record_id], metadatas=[meta])
            logger.debug(
                f"[CACHE] failure_count++ id={record_id[:8]} "
                f"→ count={meta['failure_count']}"
            )
        except Exception as e:
            logger.warning(f"[CACHE] mark_failure failed: {e}")

    # ── Hint builder ───────────────────────────────────────────────────────

    def _build_hint(
        self,
        recipes:          List[RecipeStep],
        step_instruction: str,
    ) -> str:
        """
        Format retrieved steps as a hint injected into the Tier 3 LLM prompt.
        Keeps it short — the LLM should use it as a guide, not a hard script.
        """
        lines = [
            "📋 SIMILAR PAST STEPS (for guidance only — find elements on current screen):"
        ]
        for r in recipes[:3]:
            action_desc = r.action_type
            if r.action_type == "type" and r.typed_value:
                action_desc = f"type '{(r.typed_value or '')[:30]}'"
            elif r.action_type == "click" and r.selectors:
                primary = r.selectors[0]
                if primary.get("by") in ("text", "content_desc"):
                    action_desc = f"click element with label '{primary.get('value', '')[:30]}'"
                else:
                    action_desc = "click the appropriate on-screen element"

            lines.append(f"\n  • {r.step_instruction[:60]}: {action_desc}")
            if r.direction:
                lines.append(f"  Direction: {r.direction}")
        lines.append(
            "\n⚠️ Find elements by current TEXT/LABEL; do not assume historical positions or IDs."
        )
        return "\n".join(lines)

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        count = self._col.count() if self._col else 0
        return {
            "total_records": count,
            "chroma_path":   self._chroma_path,
            "embed_model":   EMBED_MODEL,
        }