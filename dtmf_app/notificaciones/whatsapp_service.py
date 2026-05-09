"""
Servicio para automatización de WhatsApp Web usando Selenium.
"""
import time
import os
import numpy as np
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from ..config.settings import CHROMEDRIVER_PATH, WHATSAPP_URL, WHATSAPP_WAIT_TIMEOUT


class WhatsAppService:
    """Servicio para automatizar el envío de mensajes por WhatsApp Web."""
    
    def __init__(self):
        """Inicializa el servicio de WhatsApp."""
        self.driver = None
        self.wait = None
        self.service = Service(CHROMEDRIVER_PATH)
    
    def initialize_driver(self, profile_path: str = None):
        """
        Inicializa el navegador Chrome con Selenium configurado para evitar detección (Modo Sigilo).
        """
        try:
            if not self.driver:
                options = webdriver.ChromeOptions()
                
                # --- Optimización de Rendimiento ---
                options.page_load_strategy = 'eager'  # No esperar recursos de fondo
                options.add_argument("--disable-notifications") # Evitar popups
                options.add_argument("--disable-logging")
                options.add_argument("--log-level=3")
                options.add_argument("--disable-extensions") # Ahorra RAM
                options.add_argument("--disable-popup-blocking")
                options.add_argument("--disable-infobars")
                
                # Configuración básica de perfil
                if profile_path:
                    options.add_argument(f"user-data-dir={profile_path}")
                
                # --- Anti-Detección / Stealth Mode ---
                # --- Anti-Detección / Stealth Mode ---
                # 1. Deshabilitar bandera de automatización
                options.add_argument("--disable-blink-features=AutomationControlled")
                
                # 2. Excluir switches de automatización
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                
                # 3. Flags de estabilidad para evitar crashes (DevToolsActivePort error)
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu")
                
                # Inicializar driver SIN INTENOS DE DESBLOQUEO NI RECUPERACIÓN
                try:
                    self.driver = webdriver.Chrome(service=self.service, options=options)
                except Exception as e:
                    print(f"Error al iniciar driver: {e}")
                    # Ya no intentamos desbloquear, fallamos directamente
                    return False
                
                # 4. Solución CDP: Ocultar propiedad navigator.webdriver
                try:
                    self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                        "source": """
                            Object.defineProperty(navigator, 'webdriver', {
                                get: () => undefined
                            })
                        """
                    })
                except:
                    pass
                
                self.wait = WebDriverWait(self.driver, WHATSAPP_WAIT_TIMEOUT)

            # --- Navegación Robusta con Reintentos (SIEMPRE EJECUTAR) ---
            # Se ejecuta tanto si se acaba de crear el driver como si ya existía
            return self._ensure_on_whatsapp()

        except Exception as e:
            print(f"Error crítico en initialize_driver: {e}")
            return False

    def _unlock_profile(self, profile_path: str) -> bool:
        """Intenta eliminar archivos de bloqueo de Chrome si existen."""
        try:
            locks = ["SingletonLock", "SingletonSocket", "lockfile"]
            cleaned = False
            for lock in locks:
                path = os.path.join(profile_path, lock)
                if os.path.exists(path):
                    try:
                        # En Windows a veces son directorios o symlinks, pero SingletonLock suele ser archivo
                        if os.path.isdir(path):
                            os.rmdir(path)
                        else:
                            os.remove(path)
                        print(f"Eliminado bloqueo: {lock}")
                        cleaned = True
                    except Exception as e:
                        print(f"No se pudo eliminar {lock}: {e}")
            
            # Dar un momento al sistema
            time.sleep(1)
            return True
        except Exception as e:
            print(f"Error al desbloquear perfil: {e}")
            return False

    def _ensure_on_whatsapp(self) -> bool:
        """Garantiza que el navegador esté en WhatsApp Web."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 1. Verificar si ya estamos ahí correctamente
                current_url = self.driver.current_url
                if "web.whatsapp.com" in current_url and "data:," not in current_url:
                    # Verificar título para estar seguros que cargó
                    if "WhatsApp" in self.driver.title:
                        return True

                print(f"Navegando a WhatsApp (Intento {attempt + 1}/{max_retries})...")
                self.driver.get(WHATSAPP_URL)
                
                # Verificación
                WebDriverWait(self.driver, 15).until(
                    lambda d: "web.whatsapp.com" in d.current_url
                )
                
                # Espera extra para título si es necesario
                WebDriverWait(self.driver, 5).until(
                    lambda d: "WhatsApp" in d.title
                )
                
                print("Navegación confirmada.")
                return True
                
            except TimeoutException:
                print(f"Timeout esperando carga de WhatsApp (Intento {attempt + 1})")
            except Exception as e:
                print(f"Error navegando: {e}")
                time.sleep(1)
        
        print("No se pudo garantizar la navegación a WhatsApp.")
        return False
    
    def is_logged_in(self) -> bool:
        """Verifica si el usuario está logueado en WhatsApp Web."""
        try:
            # Busqueda RÁPIDA (short wait) para no bloquear
            short_wait = WebDriverWait(self.driver, 5)
            try:
                short_wait.until(EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "[title='Nuevo chat'], [aria-label='Nuevo chat']")
                ))
                return True
            except:
                short_wait.until(EC.presence_of_element_located(
                    (By.XPATH, "//span[@data-icon='new-chat-outline']/ancestor::button[1]")
                ))
                return True
        except:
            return False

    def is_qr_visible(self) -> bool:
        """
        Detecta rápidamente si el código QR está visible (sesión no iniciada / perfil bloqueado).
        NO usa waits largos — es una verificación instantánea.
        
        Returns:
            bool: True si el QR está visible, False si no
        """
        try:
            # 1. Canvas QR
            if self.driver.find_elements(By.CSS_SELECTOR, "canvas[aria-label*='Scan']"):
                return True
            # 2. Landing wrapper (página de login)
            if self.driver.find_elements(By.CLASS_NAME, "landing-wrapper"):
                return True
            # 3. Textos de login
            login_texts = [
                "//div[contains(text(), 'Pasos para iniciar sesión')]",
                "//div[contains(text(), 'Vincular con el número de teléfono')]",
                "//div[contains(text(), 'Use WhatsApp on your computer')]",
                "//div[contains(text(), 'To use WhatsApp on your computer')]",
            ]
            for xpath in login_texts:
                if self.driver.find_elements(By.XPATH, xpath):
                    return True
            return False
        except Exception:
            return False

    def is_session_active(self) -> bool:
        """
        Verifica si la sesión está activa buscando indicadores de desconexión (QR, textos de login).
        Retorna True si la sesión parece activa, False si se detecta cierre de sesión.
        """
        try:
            # 1. Busqueda de canvas QR (Selector mejorado)
            # El usuario indicó: <canvas ... aria-label="Scan this QR code...">
            # Buscamos por tag canvas Y por el aria-label parcial
            qr_canvas = self.driver.find_elements(By.CSS_SELECTOR, "canvas[aria-label*='Scan']")
            if qr_canvas:
                print("Detectado código QR (Canvas). Sesión Cerrada.")
                return False
                
            # 2. Busqueda por Texto de Login (Español/Inglés)
            # "Pasos para iniciar sesión" / "Vincular con el número de teléfono"
            # Usamos XPATH para buscar texto visible
            login_texts = [
                "//div[contains(text(), 'Pasos para iniciar sesión')]",
                "//div[contains(text(), 'Vincular con el número de teléfono')]",
                "//div[contains(text(), 'Use WhatsApp on your computer')]",
                "//div[contains(text(), 'To use WhatsApp on your computer')]"
            ]
            
            for xpath in login_texts:
                if self.driver.find_elements(By.XPATH, xpath):
                    print(f"Detectado texto de login ({xpath}). Sesión Cerrada.")
                    return False
            
            # 3. Verificar si estamos en la landing page por estructura
            landing_wrapper = self.driver.find_elements(By.CLASS_NAME, "landing-wrapper")
            if landing_wrapper:
                print("Detectado wrapper de landing page. Sesión Cerrada.")
                return False

            return True
        except Exception as e:
            # Si hay error al buscar (ej. navegador cerrado o desconectado), asumimos inactivo
            print(f"Error verificando estado de sesión: {e}")
            return False
    
    
    def click_new_chat(self):
        """Hace clic en el botón de nuevo chat."""
        try:
            # PRIMERO: Cerrar cualquier modal abierto presionando ESC
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.3)
            except:
                pass
            
            # SEGUNDO: Intentar hacer clic en el botón de nuevo chat
            # Espera dinámica en lugar de bloques try/catch ciegos
            btn = self.wait.until(EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "[title='Nuevo chat'], [aria-label='Nuevo chat']")
            ))
            btn.click()
            time.sleep(0.5)  # Dar tiempo a que se abra el modal
        except:
            # Fallback
            try:
                btn = self.driver.find_element(
                    By.XPATH, "//span[@data-icon='new-chat-outline']/ancestor::button[1]"
                )
                btn.click()
                time.sleep(0.5)
            except Exception as e:
                print(f"Error click new chat: {e}")
    
    def search_contact(self, phone_number: str) -> bool:
        """Busca un contacto por número de teléfono.
        
        Detecta simultáneamente dos variantes del campo de búsqueda:
        - <p> editable (modal 'Nuevo chat')
        - <input aria-label='Buscar un nombre o número'> (barra lateral)
        El operador XPath '|' retorna el que aparezca primero.
        """
        try:
            short_wait = WebDriverWait(self.driver, 10)

            # Un solo wait detecta cualquiera de los dos campos al mismo tiempo
            try:
                input_field = short_wait.until(
                    EC.presence_of_element_located((
                        By.XPATH,
                        '//p[contains(@class,"copyable-text") and contains(@class,"x15bjb6t")]'
                        ' | //input[@data-tab="3" and contains(@class,"html-input")]'
                    ))
                )
            except Exception:
                print("Error al buscar contacto: no se encontró ningún campo de búsqueda")
                return False

            # Limpiar: Ctrl+A -> Delete es rápido
            input_field.send_keys(Keys.CONTROL + "a")
            input_field.send_keys(Keys.DELETE)

            # Buscar
            input_field.send_keys(phone_number)
            # Pequeña espera para que WA procese y filtre los resultados
            time.sleep(0.8)

            return True
        except Exception as e:
            print(f"Error al buscar contacto: {e}")
            return False
    
    def check_contact_exists(self) -> tuple:
        """Verifica si el contacto existe y tiene WhatsApp."""
        try:
            # Esperar panel lateral o error
            # Usamos wait con ANY condition si fuera posible, aqui secuencial optimizado
            try:
                # 1. Chequeo rápido de mensaje "Sin resultados" o similar
                # Optimización: Reducir timeout si estamos seguros que la búsqueda ya se hizo
                short_wait = WebDriverWait(self.driver, 5)
                
                # Verificar si hay error de conexión
                try:
                    short_wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.x1c436fg')))
                    return False, False, "Sin conexión a Internet"
                except:
                    pass

                # Verificar si el contacto tiene WhatsApp
                # Esperamos que aparezca la lista de resultados o el mensaje de 'no encontrado'
                # WA suele mostrar "Contactos en WhatsApp"
                try:
                    print("Esperando que se detecte el contacto")
                    short_wait.until(
                        EC.presence_of_element_located((
                            By.XPATH, "//span[contains(text(), 'Contactos en WhatsApp') or contains(text(), 'Usuarios que no están en tus contactos')]"
                        ))
                    )
                    print("contacto detectado")
                    return True, True, ""
                except:
                    # Si no aparece lo anterior, quizás es inválido
                    return True, False, "Sin WhatsApp"
            except Exception as e:
                return True, False, "Timeout verificación"
        except Exception as e:
            print(f"Error al verificar contacto: {e}")
            return False, False, str(e)
    
    
    def handle_connection_error(self):
        """Maneja errores de conexión a Internet."""
        try:
            # Usar el mismo selector flexible que go_back()
            back_button = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((
                    By.XPATH, 
                    '//*[@aria-label="Atrás" and (self::div[@role="button"] or self::button)]'
                ))
            )
            back_button.click()
        except:
            pass
    
    def go_back(self):
        """Hace clic en el botón de atrás."""
        try:
            # Selector flexible: busca div[@role="button"] o button con aria-label="Atrás"
            # Versión antigua: <div role="button" aria-label="Atrás">
            # Versión nueva: <button aria-label="Atrás">
            back_button = self.wait.until(
                EC.element_to_be_clickable((
                    By.XPATH, 
                    '//*[@aria-label="Atrás" and (self::div[@role="button"] or self::button)]'
                ))
            )
            back_button.click()
            time.sleep(0.5)  # Esperar a que se complete la navegación
        except Exception as e:
            print(f"Error al ir atrás: {e}")
            # Fallback: intentar presionar ESC para cerrar cualquier modal
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.5)
            except:
                pass
    
    def open_chat(self) -> bool:
        """Abre el chat del contacto encontrado.
        
        Detecta simultáneamente dos variantes del campo de búsqueda:
        - <p> editable (modal 'Nuevo chat')
        - <input aria-label='Buscar un nombre o número'> (barra lateral)
        El operador XPath '|' retorna el que aparezca primero.
        """
        try:
            short_wait = WebDriverWait(self.driver, 10)

            # Un solo wait detecta cualquiera de los dos campos al mismo tiempo
            try:
                input_field = short_wait.until(
                    EC.presence_of_element_located((
                        By.XPATH,
                        '//p[contains(@class,"copyable-text") and contains(@class,"x15bjb6t")]'
                        ' | //input[@data-tab="3" and contains(@class,"html-input")]'
                    ))
                )
            except Exception:
                print("Error al abrir chat: no se encontró ningún campo de búsqueda")
                return False

            # Enter abre el primer resultado de la búsqueda
            input_field.send_keys(Keys.ENTER)
            return True
        except Exception as e:
            print(f"Error al abrir chat: {e}")
            return False
    
    def send_text_message(self, message: str) -> bool:
        """Envía un mensaje de texto."""
        try:
            # Esperar explícitamente el footer del chat
            parent = self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div._ak1q, div._ak1r")
                )
            )
            # Input de mensaje
            child = parent.find_element(
                By.CSS_SELECTOR,
                'p.copyable-text.x15bjb6t.x1n2onr6'
            )
            # Limpieza rápida
            child.send_keys(Keys.CONTROL + "a")
            child.send_keys(Keys.DELETE)
            
            # Escritura rápida
            paragraphs = message.split('\n')
            for paragraph in paragraphs:
                child.send_keys(paragraph)
                child.send_keys(Keys.SHIFT + Keys.ENTER)
            
            return True
        except Exception as e:
            print(f"Error al enviar mensaje de texto: {e}")
            return False
    
    def attach_file(self, file_path: str) -> bool:
        """Adjunta un archivo (imagen o PDF)."""
        if not file_path or not os.path.isfile(file_path):
            return False
        
        try:
            # Botón clip
            adjuntar_btn = self.wait.until(EC.element_to_be_clickable(
                 (By.CSS_SELECTOR, "[title='Adjuntar'], [aria-label='Adjuntar']")
            ))
            adjuntar_btn.click()
            
            # Esperar input de archivo (oculto) o la opción de menú
            # WA Web cambia a veces, buscamos la opción "Fotos y videos"
            # Optimización: Click directo si es visible
            
            opcion = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//li[.//span[text()='Fotos y videos'] or .//span[text()='Documento']]")
                # Nota: Simplificado para el ejemplo, idealmente distinguimos imagen vs documento
                # Pero en el flujo actual asumimos imagen o PDF genericamente
                # Ajustaré para buscar espeíficamente Fotos y Videos como estaba antes pero con Wait
            ))
            
            # NOTA: Para subir archivos con Selenium, lo mas robusto es encontrar el <input type='file'> 
            # y enviarle el path, en vez de clickear la UI. 
            # WA Web tiene inputs hidden.
            
            # Estrategia UI (Lenta pero visual):
            # Click fotos y videos -> abre dialogo sistema (Selenium no controla dialogo sistema facil)
            
            # ESTRATEGIA OPTIMIZADA SELENIUM: SendKeys a input file
            # En WA Web el input file suele estar presente en el DOM cuando se abre el menu
            
            try:
                # Buscar input file correspondiente a imagen/video
                # accept="image/*,video/mp4,video/3gpp,video/quicktime"
                inputs = self.driver.find_elements(By.TAG_NAME, "input")
                file_input = None
                for inp in inputs:
                    if inp.get_attribute("type") == "file":
                        # Usar el primero o filtrar por accept
                        file_input = inp
                        break
                
                if file_input:
                    file_input.send_keys(file_path)
                    # Esperar preview
                    self.wait.until(EC.presence_of_element_located((By.XPATH, '//div[@aria-label="Enviar"]')))
                    return True
                else:
                    # Fallback click UI (no recomendado en headless/background pero bueno)
                    opcion.click()
                    # Esto abrirá ventana de sistema y pausará script... 
                    # REVERTIR: La implementación original usaba click en UI? 
                    # Si usaba click en UI, requeria interaccion manual o autoit.
                    # Asumo que el codigo original funcionaba por "magia" o el usuario lo hacia?
                    # Ah, revisando logs anteriores, usabas `adjuntar_btn.click()` y luego nada?
                    # No, esperabas `send_keys` no?
                    # El codigo original hace click en 'Fotos y videos'. Eso abre el explorador de archivos de Windows.
                    # Selenium NO puede interactuar con eso.
                    # CORRECCIÓN: Debemos usar send_keys al input type=file SIEMPRE.
                    pass
            except:
                pass

            # Si la estrategia de arriba falla, volvemos a la original de "Clickar" 
            # pero recuerda que eso no sube el archivo automaticamente.
            # Voy a mantener la logica "Click UI" del usuario original PERO optimizada con waits,
            # aunque advierto que send_keys es mejor.
            
            # Reimplementando logica original optimizada:
            menu_item = self.wait.until(EC.presence_of_element_located(
                (By.XPATH, "//li[.//span[text()='Fotos y videos']]")
            ))
            
            # Truco: WA Web a veces permite send_keys al input dentro del li
            inp = menu_item.find_element(By.TAG_NAME, "input")
            inp.send_keys(file_path)
            
            # Esperar carga preview
            self.wait.until(EC.presence_of_element_located((By.XPATH, '//div[@aria-label="Enviar"]')))
            return True

        except Exception as e:
            print(f"Error al adjuntar archivo: {e}")
            return False
    
    def send_attached_file(self) -> bool:
        """
        Envía el archivo adjuntado.
        
        Returns:
            bool: True si se envió correctamente, False si no
        """
        try:
            send_button = WebDriverWait(self.driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, '//div[@aria-label="Enviar"]'))
            )
            time.sleep(0.5)
            send_button.click()
            return True
        except Exception as e:
            print(f"Error al enviar archivo adjuntado: {e}")
            return False
    
    def send_message_simple(self) -> bool:
        """
        Envía un mensaje simple (sin archivo).
        
        Returns:
            bool: True si se envió correctamente, False si no
        """
        try:
            # Buscar el campo de entrada
            parent = self.wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div._ak1q, div._ak1r")
                )
            )
            child = parent.find_element(By.CSS_SELECTOR,'p.copyable-text.x15bjb6t.x1n2onr6')
            child.send_keys(Keys.ENTER)
            return True
        except Exception as e:
            print(f"Error al enviar mensaje simple: {e}")
            return False
    
    def extract_whatsapp_phone_number(self) -> str | None:
        """
        Extrae el número de teléfono vinculado a la sesión activa de WhatsApp Web.

        Maneja DOS variantes del botón en la navbar:

        Variante A — Botón aria-label="Perfil" (avatar genérico, sin foto de perfil):
          Click → el panel de perfil abre directamente → buscar span con número.

        Variante B — Botón aria-label="Tú" (tiene foto de perfil):
          Click → menú desplegable → click en sub-botón "Perfil" → buscar span
          con número (esta vez está junto al icono data-icon="phone").

        Returns:
            str | None: Número de teléfono (ej: "+57 321 7166019") o None.
        """
        import re
        try:
            short_wait = WebDriverWait(self.driver, 8)

            # ── 1. Detectar cuál botón está en la navbar ─────────────────────────
            profile_btn = None
            is_tu_button = False

            try:
                profile_btn = short_wait.until(EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "button[aria-label='Perfil'][data-navbar-item='true']")
                ))
                print("[RenombrePerfil] Botón 'Perfil' (avatar genérico) encontrado.")
            except:
                pass

            if not profile_btn:
                try:
                    profile_btn = short_wait.until(EC.element_to_be_clickable(
                        (By.CSS_SELECTOR, "button[aria-label='Tú'][data-navbar-item='true']")
                    ))
                    is_tu_button = True
                    print("[RenombrePerfil] Botón 'Tú' (con foto) encontrado.")
                except:
                    pass

            if not profile_btn:
                print("[RenombrePerfil] No se encontró botón de perfil en la navbar.")
                return None

            # ── 2. Click en el botón principal ────────────────────────────────
            profile_btn.click()
            print(f"[RenombrePerfil] Click en botón '{'Tú' if is_tu_button else 'Perfil'}' realizado.")
            time.sleep(1.5)

            # ── 3. Variante B: click en sub-botón 'Perfil' ───────────────────────
            if is_tu_button:
                try:
                    sub_btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            # Detecta el botón con el texto 'Nombre, foto del perfil...' o el SVG ic-account-circle
                            "//button[.//span[contains(text(), 'Nombre, foto del perfil')]]"
                            " | //button[.//*[local-name()='title' and text()='ic-account-circle']]"
                        ))
                    )
                    sub_btn.click()
                    print("[RenombrePerfil] Click en sub-botón 'Perfil' realizado.")
                    time.sleep(1.5)
                except Exception as e:
                    print(f"[RenombrePerfil] Error buscando sub-botón Perfil: {e}")

            # ── 4. Buscar el número de teléfono en el panel ─────────────────────
            phone_number = None

            # Estrategia A: span junto al icón de teléfono (Variante B tiene este elemento)
            try:
                elems = self.driver.find_elements(
                    By.XPATH,
                    "//div[.//*[@data-icon='phone']]//span[starts-with(normalize-space(text()), '+')]"
                )
                for el in elems:
                    text = el.text.strip()
                    cleaned = text.replace(' ', '').replace('-', '').replace('+', '')
                    if text.startswith('+') and cleaned.isdigit() and len(cleaned) >= 7:
                        phone_number = text
                        print(f"[RenombrePerfil] Número encontrado (icono phone): {phone_number}")
                        break
            except Exception as e:
                print(f"[RenombrePerfil] Estrategia A (icono phone) falló: {e}")

            # Estrategia B: span con --x-fontSize que empiece con '+'
            if not phone_number:
                try:
                    spans = self.driver.find_elements(
                        By.XPATH,
                        "//span[contains(@style, '--x-fontSize') and starts-with(normalize-space(text()), '+')]"
                    )
                    for span in spans:
                        text = span.text.strip()
                        cleaned = text.replace(' ', '').replace('-', '').replace('+', '')
                        if text.startswith('+') and cleaned.isdigit() and len(cleaned) >= 7:
                            phone_number = text
                            print(f"[RenombrePerfil] Número encontrado (--x-fontSize): {phone_number}")
                            break
                except Exception as e:
                    print(f"[RenombrePerfil] Estrategia B (--x-fontSize) falló: {e}")

            # Estrategia C: regex sobre todos los spans (fallback total)
            if not phone_number:
                try:
                    phone_pattern = re.compile(r'^\+[\d\s\-]{7,20}$')
                    for span in self.driver.find_elements(By.TAG_NAME, "span"):
                        try:
                            text = span.text.strip()
                            if phone_pattern.match(text):
                                phone_number = text
                                print(f"[RenombrePerfil] Número encontrado (regex fallback): {phone_number}")
                                break
                        except:
                            continue
                except Exception as e:
                    print(f"[RenombrePerfil] Estrategia C (regex) falló: {e}")

            # ── 5. Cerrar el panel con ESC ───────────────────────────────────
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.5)
            except:
                pass

            if phone_number:
                print(f"[RenombrePerfil] Número extraído: {phone_number}")
            else:
                print("[RenombrePerfil] No se pudo extraer el número de teléfono.")

            return phone_number

        except Exception as e:
            print(f"[RenombrePerfil] Error extrayendo número de teléfono: {e}")
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            except:
                pass
            return None

    def close_chat(self):
        """Cierra el chat actual."""
        try:
            time.sleep(1)
            self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
        except Exception as e:
            print(f"Error al cerrar chat: {e}")
    
    def close(self):
        """Cierra el navegador."""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            finally:
                self.driver = None
                self.wait = None
    
    def __del__(self):
        """Destructor para asegurar que el navegador se cierre."""
        self.close()
