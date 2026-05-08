"""
Servicio de monitoreo de mensajes nuevos en WhatsApp.
Adaptado para integrarse con el sistema de marketing.
"""
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
from datetime import datetime


class WhatsAppMonitorService:
    """Servicio para monitorear mensajes nuevos en WhatsApp y enviar notificaciones."""
    
    def __init__(self, driver, notification_group=None, notification_backup=None,
                 notification_contact=None, profile_name="Desconocido"):
        """
        Inicializa el servicio de monitoreo.
        
        Args:
            driver: Instancia de Selenium WebDriver ya inicializada
            notification_group: Nombre del grupo de WhatsApp (PRIORIDAD principal)
            notification_backup: Número celular de respaldo (si el grupo falla)
            notification_contact: DEPRECATED - equivale a notification_group para compatibilidad
            profile_name: Nombre del perfil de navegador que se está usando
        """
        self.driver = driver
        # Prioridad: notification_group. Si no se pasa, usar notification_contact (backward compat)
        self.notification_group = notification_group or notification_contact
        self.notification_backup = notification_backup
        # Alias para compatibilidad con código antiguo
        self.notification_contact = self.notification_group
        self.profile_name = profile_name
        self.wait = WebDriverWait(self.driver, 10)
        # Cambio: Usar diccionario para guardar estados y detectar cambios 'reales'
        self.chat_states = {}  # Format: {name: "preview|time|count"}
        # Nuevo: Set para rastrear a quiénes ya se les envió auto-respuesta en esta sesión
        self.replied_chats = set()


        
    def obtener_chats_no_leidos(self):
        """Detecta y obtiene información de los chats con mensajes no leídos."""
        try:
            # Esperar a que cargue la lista de chats
            time.sleep(2)
            
            # Buscar todos los chats con badge de mensajes no leídos
            # Usamos el selector específico que sabemos que funciona para español
            chats_no_leidos = self.driver.find_elements(
                By.CSS_SELECTOR,
                'div[role="row"]'
            )
            
            print(f"[Monitor] Total de elementos 'row' encontrados: {len(chats_no_leidos)}")
            
            chats_detectados = []
            
            for idx, chat in enumerate(chats_no_leidos):
                try:
                    # Buscar el badge de mensajes no leídos dentro del chat
                    # Selector: span con aria-label que contiene "mensaje" y "no leído"
                    badge = chat.find_element(
                        By.CSS_SELECTOR,
                        'span[aria-label*="mensaje"][aria-label*="no leído"]'
                    )
                    
                    if badge:
                        # Extraer información del chat
                        try:
                            # Nombre del chat
                            nombre_elem = chat.find_element(
                                By.CSS_SELECTOR,
                                'span[dir="auto"][title]'
                            )
                            nombre = nombre_elem.get_attribute('title')
                            
                            # Número de mensajes no leídos
                            num_mensajes = badge.get_attribute('aria-label')
                            
                            # Vista previa y hora
                            preview = "No disponible"
                            hora = "No disponible"
                            
                            try:
                                # Intentar extraer preview del texto visible
                                lines = chat.text.split('\n')
                                if len(lines) >= 2:
                                    # Normalmente: [0] Hora, [1] Nombre, [2] Mensaje... varia según layout
                                    # Estrategia segura: última línea suele ser el mensaje, segunda línea suele ser hora
                                    preview = lines[-1]
                                    if len(lines) > 1:
                                        hora = lines[1]
                            except: pass
                            
                            chat_info = {
                                'nombre': nombre,
                                'mensajes_no_leidos': num_mensajes,
                                'preview': preview[:50] + '...' if len(preview) > 50 else preview,
                                'hora': hora,
                                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                'elemento': chat
                            }
                            
                            chats_detectados.append(chat_info)
                            print(f"[Monitor] 📥 Chat no leído detectado: {nombre} ({num_mensajes})")
                            
                        except Exception as e:
                            print(f"[Monitor] Error al extraer info del chat {idx}: {e}")
                            continue
                            
                except NoSuchElementException:
                    # No tiene badge de no leído
                    continue
                except Exception as e:
                    continue
            
            print(f"[Monitor] Total de chats no leídos detectados: {len(chats_detectados)}")
            return chats_detectados
            
        except Exception as e:
            print(f"[Monitor] Error al obtener chats: {e}")
            return []
    
    def detectar_nuevos_chats(self, chats_actuales, force_all=False):
        """
        Detecta qué chats tienen mensajes nuevos comparando con el estado anterior.
        Ahora compara contenido/hora, no solo nombres.
        
        Args:
            chats_actuales: Lista de chats detectados actualmente (no leídos)
            force_all: Si es True, procesa TODOS los chats sin importar si ya fueron vistos
                      (útil para modo Monitor independiente)
            
        Returns:
            Lista de chats que requieren notificación
        """
        print(f"[Monitor] Analizando {len(chats_actuales)} chats activos...")
        
        # Si force_all está activo, procesar todos sin filtro
        if force_all:
            print(f"[Monitor] 🔄 Modo FORCE_ALL activo - procesando todos los chats no leídos")
            for chat in chats_actuales:
                nombre = chat.get('nombre', 'Desconocido')
                print(f"[Monitor] 📨 Chat pendiente: '{nombre}'")
            return chats_actuales
        
        chats_para_notificar = []
        nuevos_estados = {}

        for chat in chats_actuales:
            try:
                nombre = chat['nombre']
                # Crear firma única del estado actual
                estado_actual = f"{chat['preview']}|{chat['hora']}|{chat['mensajes_no_leidos']}"
                nuevos_estados[nombre] = estado_actual
                
                # Verificar si este chat ya fue visto y si ha cambiado
                if nombre in self.chat_states:
                    estado_anterior = self.chat_states[nombre]
                    
                    if estado_anterior != estado_actual:
                        print(f"[Monitor] 🔔 Nuevo mensaje en '{nombre}':")
                        print(f"   Anterior: {estado_anterior}")
                        print(f"   Actual:   {estado_actual}")
                        chats_para_notificar.append(chat)
                    else:
                        # El chat sigue ahí igual que antes (quizás falló al marcarse leído), 
                        # no notificamos de nuevo para evitar spam masivo del mismo mensaje
                        print(f"[Monitor] Chat '{nombre}' sin cambios desde última revisión")
                else:
                    # Chat no estaba en la memoria reciente (es nuevo o reapareció)
                    print(f"[Monitor] 🆕 Chat entrante detectado: '{nombre}'")
                    chats_para_notificar.append(chat)
            
            except Exception as e:
                print(f"[Monitor] Error analizando chat: {e}")
                # En caso de error, lo agregamos por si acaso
                chats_para_notificar.append(chat)
        
        # Actualizar nuestra base de datos de estados
        # Si un chat ya no está en 'chats_actuales' (porque fue leído), desaparecerá de aquí
        # y si vuelve a aparecer luego, será tratado como nuevo. Correcto.
        self.chat_states = nuevos_estados
        
        if chats_para_notificar:
            print(f"[Monitor] Total para notificar: {len(chats_para_notificar)}")
        else:
            print(f"[Monitor] No hay novedades para notificar")
        
        return chats_para_notificar
    
    def marcar_chat_como_leido(self, chat_info, close_after=True):
        """
        Hace clic sobre el chat para abrirlo y marcarlo como leído.
        
        Args:
            chat_info: Diccionario con información del chat (incluye 'elemento')
            close_after: Si es True, cierra el chat después de abrirlo
        
        Returns:
            True si se pudo abrir el chat, False en caso contrario
        """
        try:
            elemento_chat = chat_info.get('elemento')
            if not elemento_chat:
                print(f"[Monitor] ⚠ No se encontró elemento del chat para marcar como leído")
                return False
            
            print(f"[Monitor] Haciendo clic en chat '{chat_info['nombre']}' para marcarlo como leído...")
            
            print(f"[Monitor] Haciendo clic en chat '{chat_info['nombre']}' para marcarlo como leído...")
            
            # Hacer clic en el elemento del chat
            try:
                chat_info['elemento'].click()
            except Exception as e:
                print(f"[Monitor] ⚠ Click original falló ({type(e).__name__}), intentando re-buscar chat por nombre...")
                try:
                    # Intento de recuperación: buscar por el título exacto
                    # Usamos XPATH para ir del titulo al row
                    nombre = chat_info['nombre']
                    # Escape simple de comillas si fuera necesario (aunque nombres de WA suelen ser safe)
                    xpath = f"//span[@title='{nombre}']/ancestor::div[@role='row']"
                    nuevo_elemento = self.driver.find_element(By.XPATH, xpath)
                    nuevo_elemento.click()
                    print(f"[Monitor] ✓ Elemento re-encontrado y clickeado")
                except Exception as e2:
                    print(f"[Monitor] ❌ Error fatal al intentar re-clickear chat: {e2}")
                    return False

            time.sleep(1)  # Esperar que se abra el chat
            
            print(f"[Monitor] ✓ Chat '{chat_info['nombre']}' abierto y marcado como leído")
            
            if close_after:
                # Cerrar el chat (presionar ESC)
                try:
                    self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
                except: pass
                time.sleep(0.5)
            
            return True
            
        except Exception as e:
            print(f"[Monitor] Error al marcar chat como leído: {e}")
            return False
    
    def limpiar_mensaje(self, mensaje):
        """
        Limpia caracteres especiales del mensaje que pueden causar problemas en Selenium.
        
        Args:
            mensaje: Texto del mensaje a limpiar
            
        Returns:
            Mensaje limpio y seguro para Selenium
        """
        import re
        
        # Eliminar emojis específicos que causan problemas
        mensaje_limpio = re.sub(r'[\U00010000-\U0010ffff]', '', mensaje)
        
        return mensaje_limpio.strip()
    
    def _enviar_a_contacto(self, contacto, mensaje_limpio, whatsapp_service):
        """
        Intenta enviar un mensaje a un contacto o grupo específico.

        Flujo:
        ── VÍA RÁPIDA (sin Nuevo Chat): ─────────────────────────────────────
          1. Busca el <input> de la barra lateral y escribe el nombre del grupo.
          2. Presiona ENTER.
          3. Espera hasta 5 s a que aparezca el campo de mensaje del chat.
          4. Si aparece → chat abierto, salta al envío.
          5. Si NO aparece → activa el flujo normal.

        ── FLUJO NORMAL (con Nuevo Chat): ───────────────────────────────────
          1. click_new_chat()
          2. search_contact()
          3. check_contact_exists() solo para números
          4. open_chat()
          → envío final

        Returns:
            True si el envío fue exitoso, False en caso contrario.
        """
        import re
        try:
            chat_abierto = False

            # ── VÍA RÁPIDA ────────────────────────────────────────────────────
            print(f"[Monitor] ⚡ Vía rápida (sin 'Nuevo Chat') → '{contacto}'")
            try:
                fast_wait = WebDriverWait(self.driver, 5)

                # Detectar el campo de búsqueda de la barra lateral
                search_input = fast_wait.until(
                    EC.presence_of_element_located((
                        By.XPATH,
                        '//p[contains(@class,"copyable-text") and contains(@class,"x15bjb6t")]'
                        ' | //input[@data-tab="3" and contains(@class,"html-input")]'
                    ))
                )

                # Limpiar y escribir el nombre del grupo
                search_input.send_keys(Keys.CONTROL + "a")
                search_input.send_keys(Keys.DELETE)
                search_input.send_keys(contacto)
                time.sleep(0.8)  # Dar tiempo a que aparezcan los resultados

                # Presionar ENTER para abrir el primer resultado
                search_input.send_keys(Keys.ENTER)

                # Verificar si el chat se abrió (aparece el footer con el campo de mensaje)
                try:
                    msg_wait = WebDriverWait(self.driver, 5)
                    msg_wait.until(
                        EC.presence_of_element_located((
                            By.CSS_SELECTOR, "div._ak1q, div._ak1r"
                        ))
                    )
                    print("[Monitor] ✓ Vía rápida exitosa — chat abierto")
                    chat_abierto = True
                except Exception:
                    print("[Monitor] ⚠ Chat no se abrió en 5s → activando flujo normal")
                    chat_abierto = False

            except Exception as e:
                print(f"[Monitor] ⚠ Vía rápida no disponible ({e}) → flujo normal")
                chat_abierto = False

            # ── FLUJO NORMAL (solo si la vía rápida no abrió el chat) ─────────
            if not chat_abierto:
                print(f"[Monitor] Paso 1/4: Abriendo 'Nuevo chat' para → '{contacto}'...")
                whatsapp_service.click_new_chat()
                print("[Monitor] ✓ 'Nuevo chat' abierto")

                print(f"[Monitor] Paso 2/4: Buscando contacto '{contacto}'...")
                if not whatsapp_service.search_contact(contacto):
                    print(f"[Monitor] ✗ Error al buscar '{contacto}'")
                    try:
                        whatsapp_service.go_back()
                    except:
                        pass
                    return False
                print("[Monitor] ✓ Contacto buscado")

                # Verificación de existencia solo para números (no grupos/nombres)
                is_group_or_name = bool(re.search('[a-zA-Z]', contacto))
                if is_group_or_name:
                    print("[Monitor] Detectado nombre/grupo → saltando validación numérica")
                    time.sleep(1)
                else:
                    print("[Monitor] Paso 3/4: Verificando existencia del contacto (número)...")
                    found, has_whatsapp, error_msg = whatsapp_service.check_contact_exists()
                    if not found or not has_whatsapp:
                        print(f"[Monitor] ✗ Contacto no encontrado o sin WhatsApp: {error_msg}")
                        try:
                            whatsapp_service.go_back()
                        except:
                            pass
                        return False
                    print("[Monitor] ✓ Contacto verificado")

                print("[Monitor] Paso 4/4: Abriendo chat...")
                if not whatsapp_service.open_chat():
                    print("[Monitor] ✗ Error al abrir el chat")
                    try:
                        whatsapp_service.go_back()
                    except:
                        pass
                    return False
                print("[Monitor] ✓ Chat abierto")

            # ── PASO FINAL: Enviar el mensaje (común a ambas vías) ────────────
            print("[Monitor] Enviando mensaje...")
            if not whatsapp_service.send_text_message(mensaje_limpio):
                print("[Monitor] ✗ Error al escribir el mensaje")
                return False
            if not whatsapp_service.send_message_simple():
                print("[Monitor] ✗ Error al enviar el mensaje")
                return False

            print(f"[Monitor] ✓ Notificación enviada correctamente a '{contacto}'")
            time.sleep(2)
            whatsapp_service.close_chat()
            time.sleep(2)
            return True

        except Exception as e:
            print(f"[Monitor] ❌ Error enviando a '{contacto}': {e}")
            import traceback
            traceback.print_exc()
            try:
                whatsapp_service.go_back()
            except:
                pass
            return False

    def enviar_notificacion(self, chat_info, whatsapp_service, mensajes_completos=None):
        """
        Envía una notificación usando PRIORIDAD DE GRUPO con RESPALDO a número celular.
        
        Orden de intento:
          1. notification_group (nombre del grupo) — prioridad
          2. notification_backup (número celular)  — solo si el grupo falla
        
        Args:
            chat_info: Diccionario con información del chat
            whatsapp_service: Instancia de WhatsAppService para enviar el mensaje
            mensajes_completos: Lista de mensajes leídos (opcional)
            
        Returns:
            True si alguno de los dos intentos tuvo éxito, False si ambos fallaron
        """
        if not self.notification_group and not self.notification_backup:
            print("[Monitor] ⚠️ No hay destino de notificación configurado (ni grupo ni respaldo)")
            return False

        # Construir el mensaje de notificación
        contenido_mensajes = ""
        if mensajes_completos:
            contenido_mensajes = "\n".join(mensajes_completos)
        else:
            contenido_mensajes = f"Preview: {chat_info['preview']}"

        mensaje = f"""Perfil: {self.profile_name}
Nombre: {chat_info['nombre']}
Hora: {chat_info['hora']}
Detectado: {chat_info['timestamp']}

--- MENSAJES ---
{contenido_mensajes}"""

        mensaje_limpio = self.limpiar_mensaje(mensaje)

        print(f"\n[Monitor] 📤 Enviando notificación sobre: {chat_info['nombre']}")

        # ── INTENTO 1: Grupo (prioridad) ──────────────────────────────────────
        if self.notification_group:
            print(f"[Monitor] 🥇 Intento 1/2 → GRUPO: '{self.notification_group}'")
            exito = self._enviar_a_contacto(self.notification_group, mensaje_limpio, whatsapp_service)
            if exito:
                print(f"[Monitor] ✅ Notificación enviada al grupo '{self.notification_group}'")
                return True
            else:
                print(f"[Monitor] ⚠️ Falló envío al grupo '{self.notification_group}'")
                if self.notification_backup:
                    print(f"[Monitor] 🔄 Activando respaldo → número celular: '{self.notification_backup}'")
                else:
                    print("[Monitor] ❌ No hay número de respaldo configurado. Notificación perdida.")
                    return False

        # ── INTENTO 2: Número celular (respaldo) ──────────────────────────────
        if self.notification_backup:
            print(f"[Monitor] 🥈 Intento 2/2 → RESPALDO: '{self.notification_backup}'")
            exito = self._enviar_a_contacto(self.notification_backup, mensaje_limpio, whatsapp_service)
            if exito:
                print(f"[Monitor] ✅ Notificación enviada al número de respaldo '{self.notification_backup}'")
                return True
            else:
                print(f"[Monitor] ❌ Falló también el número de respaldo '{self.notification_backup}'")

        print("[Monitor] ❌ Todos los intentos de notificación fallaron.")
        return False
    
    def monitorear_y_notificar(self, whatsapp_service, max_time=5, auto_reply_text=None):
        """
        Monitorea mensajes nuevos y envía notificaciones si los hay.
        Esta función está diseñada para ser llamada antes de cada envío.
        
        Args:
            whatsapp_service: Instancia de WhatsAppService
            max_time: Tiempo máximo en segundos para el monitoreo (default: 5)
            auto_reply_text: Texto para respuesta automática (opcional). Si se define, se envía al chat.
            
        Returns:
            Tiempo usado en el monitoreo (en segundos)
        """
        if not self.notification_group and not self.notification_backup:
            print("[Monitor] Monitor deshabilitado (sin grupo ni número de notificación)")
            return 0  # Monitor deshabilitado
        
        print(f"\n[Monitor] ═══════════════════════════════════════")
        print(f"[Monitor] Iniciando monitoreo de mensajes nuevos")
        print(f"[Monitor] 🥇 Grupo (prioridad): {self.notification_group or '❌ No configurado'}")
        print(f"[Monitor] 🥈 Respaldo (celular): {self.notification_backup or '❌ No configurado'}")
        if auto_reply_text:
            print(f"[Monitor] 🤖 MODO AUTO-RESPUESTA ACTIVO")
            print(f"[Monitor] Mensaje: '{auto_reply_text}'")
        print(f"[Monitor] Tiempo máximo: {max_time}s")
        print(f"[Monitor] ═══════════════════════════════════════")
        
        start_time = time.time()
        
        try:
            # Obtener chats no leídos
            print("[Monitor] Buscando chats no leídos...")
            chats = self.obtener_chats_no_leidos()
            
            # Detectar nuevos chats (force_all=True para procesar TODOS en modo monitor)
            print("[Monitor] Analizando chats nuevos...")
            chats_nuevos = self.detectar_nuevos_chats(chats, force_all=True)
            
            if chats_nuevos:
                print(f"\n[Monitor] 🆕 Se detectaron {len(chats_nuevos)} chat(s) nuevo(s)")
                
                for idx, chat in enumerate(chats_nuevos, 1):
                    # Verificar si aún tenemos tiempo
                    elapsed = time.time() - start_time
                    if elapsed >= max_time:
                        print(f"[Monitor] ⏱ Tiempo de monitoreo agotado, omitiendo notificaciones restantes")
                        break
                    
                    print(f"\n[Monitor] Procesando notificación {idx}/{len(chats_nuevos)}...")

                    # ── Filtro de grupos: solo procesar chats de usuarios reales ──
                    # Los contactos reales tienen nombre que comienza con '+' (ej: +57 320 4901850)
                    # Los grupos tienen nombres de texto (ej: ventas_mkt3, Soporte)
                    nombre_chat = chat.get('nombre', '')
                    if not nombre_chat.startswith('+'):
                        print(f"[Monitor] 🚫 Chat de grupo detectado: '{nombre_chat}' → OMITIENDO (solo se procesan contactos +XX)")
                        continue

                    if not self.marcar_chat_como_leido(chat, close_after=False):
                        print(f"[Monitor] ⚠ No se pudo marcar como leído, continuando...")
                        continue
                    
                    # PASO 1.5: Contar mensajes de salida para determinar acción
                    num_salidas = self.contar_mensajes_salida()
                    
                    # PASO 1.6: Leer los mensajes completos (con filtro de auto-respuestas)
                    mensajes = self.leer_mensajes_chat_abierto()

                    # ── Si la lista quedó vacía, todos los mensajes eran auto-respuestas ──
                    # En ese caso cerrar chat y NO notificar
                    if not mensajes:
                        print(f"[Monitor] 🔕 '{nombre_chat}' — solo auto-respuestas detectadas → no notificar")
                        try:
                            self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
                        except: pass
                        time.sleep(0.5)
                        continue

                    # ═══════════════════════════════════════════════════════════════
                    # LÓGICA MEJORADA DE AUTO-RESPUESTA Y NOTIFICACIÓN
                    # Basada en el conteo de mensajes de salida en el historial:
                    #
                    # 0 mensajes de salida → Chat completamente nuevo
                    #     Acción: SOLO NOTIFICAR (para que operador lo vea)
                    #
                    # 1 mensaje de salida → Primera respuesta enviada
                    #     Acción: SOLO AUTO-RESPONDER (dar seguimiento automático)
                    #
                    # 2+ mensajes de salida → Conversación ya establecida
                    #     Acción: SOLO NOTIFICAR (evitar spam de auto-respuestas)
                    # ═══════════════════════════════════════════════════════════════
                    
                    debe_notificar = False
                    debe_auto_responder = False
                    
                    if num_salidas == 0:
                        # Chat nuevo sin interacción previa
                        print(f"[Monitor] 📋 Chat sin mensajes de salida previos")
                        print(f"[Monitor] → SOLO NOTIFICAR (chat nuevo)")
                        debe_notificar = True
                        debe_auto_responder = False
                        
                    elif num_salidas == 1:
                        # Solo una respuesta previa (probablemente la auto-respuesta o respuesta manual)
                        print(f"[Monitor] 🔁 Chat con 1 mensaje de salida")
                        
                        if auto_reply_text:
                            print(f"[Monitor] → SOLO AUTO-RESPONDER (dar seguimiento)")
                            debe_notificar = False
                            debe_auto_responder = True
                        else:
                            # Si no hay auto-respuesta configurada, solo notificar
                            print(f"[Monitor] → SOLO NOTIFICAR (auto-respuesta desactivada)")
                            debe_notificar = True
                            debe_auto_responder = False
                        
                    else:  # 2 o más mensajes de salida
                        # Ya existe conversación establecida
                        print(f"[Monitor] 💬 Chat con {num_salidas} mensajes de salida (conversación establecida)")
                        print(f"[Monitor] → SOLO NOTIFICAR (evitar spam de auto-respuestas)")
                        debe_notificar = True
                        debe_auto_responder = False
                    
                    # EJECUTAR AUTO-RESPUESTA si corresponde
                    if debe_auto_responder and auto_reply_text:
                        print(f"[Monitor] 🤖 Enviando auto-respuesta...")
                        try:
                            # Asegurar foco antes de escribir
                            try:
                                input_box = self.driver.switch_to.active_element
                                input_box.click()
                            except: pass
                            
                            if whatsapp_service.send_text_message(auto_reply_text):
                                time.sleep(0.5)
                                whatsapp_service.send_message_simple()
                                print(f"[Monitor] ✅ Auto-respuesta enviada")
                                time.sleep(1)
                            else:
                                print(f"[Monitor] ❌ Falló al escribir auto-respuesta")
                        except Exception as e:
                            print(f"[Monitor] ❌ Error enviando auto-respuesta: {e}")
                    
                    # Cerrar el chat ahora (ESC)
                    try:
                        self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
                    except: pass
                    time.sleep(0.5)
                    
                    # EJECUTAR NOTIFICACIÓN si corresponde
                    if debe_notificar:
                        resultado = self.enviar_notificacion(chat, whatsapp_service, mensajes_completos=mensajes)
                        if resultado:
                            print(f"[Monitor] ✅ Notificación {idx} enviada exitosamente")
                        else:
                            print(f"[Monitor] ❌ Falló notificación {idx}")
                    else:
                        print(f"[Monitor] ⏭️ Notificación {idx} omitida (según lógica de conteo de mensajes)")
            else:
                print("[Monitor] ℹ️ No hay mensajes nuevos para notificar")
            
        except Exception as e:
            print(f"[Monitor] ❌ Error en monitoreo: {e}")
            import traceback
            traceback.print_exc()
        
        # Retornar el tiempo usado
        elapsed_time = time.time() - start_time
        print(f"\n[Monitor] ═══════════════════════════════════════")
        print(f"[Monitor] Monitoreo completado en {elapsed_time:.2f}s")
        print(f"[Monitor] ═══════════════════════════════════════\n")
        return elapsed_time

    def _extraer_hora_fila(self, fila):
        """
        Extrae y normaliza la hora (H:MM) de una fila de mensaje del chat.
        Siempre retorna solo 'H:MM' (sin a.m./p.m. ni texto extra) para
        que la comparación entre mensajes de salida y entrada sea consistente.

        Returns:
            str con la hora "H:MM" o "" si no se pudo extraer
        """
        import re

        def _normalizar(texto):
            """Extrae solo la parte H:MM de un string como '2:03 p.m.' o '14:03'."""
            match = re.search(r'(\d{1,2}:\d{2})', texto)
            return match.group(1) if match else ""

        try:
            hora_elem = fila.find_element(By.CSS_SELECTOR, "span[dir='auto'].x1c4vz4f")
            return _normalizar(hora_elem.text.strip())
        except:
            pass
        # Fallback: parsear data-pre-plain-text → "[HH:MM, DD/MM/YYYY] Nombre: "
        try:
            copyable = fila.find_element(By.CSS_SELECTOR, "div.copyable-text")
            pre_text = copyable.get_attribute("data-pre-plain-text") or ""
            return _normalizar(pre_text)
        except:
            pass
        return ""


    def leer_mensajes_chat_abierto(self):
        """
        Lee los mensajes del chat abierto actualmente.
        Recorre de abajo hacia arriba hasta encontrar el último mensaje enviado por nosotros (outgoing).
        Captura todos los mensajes entrantes (incoming) posteriores a ese.

        FILTRO DE RESPUESTAS AUTOMÁTICAS (doble condición):
        Un mensaje entrante se omite SOLO si cumple AMBAS condiciones:
          1. Su hora (HH:MM) es igual a la hora del último mensaje de salida
          2. El texto tiene 70 caracteres o menos
        Si solo se cumple una condición, el mensaje se incluye (es respuesta real).

        LÍMITE: Si no se encuentra mensaje de salida, solo lee los últimos 5 mensajes.

        Returns:
            Lista de mensajes (texto) ordenados cronológicamente
        """
        try:
            print("[Monitor] Leyendo mensajes del chat abierto...")
            filas = self.driver.find_elements(By.CSS_SELECTOR, "div[role='row']")

            mensajes_nuevos = []
            MAX_MENSAJES_SIN_SALIDA = 5

            # ── Paso 1: Encontrar la hora del ÚLTIMO mensaje de salida ──────────
            hora_ultimo_salida = ""
            for fila in reversed(filas):
                try:
                    if fila.find_elements(By.CSS_SELECTOR, "div.message-out"):
                        hora_ultimo_salida = self._extraer_hora_fila(fila)
                        print(f"[Monitor] 🕐 Hora del último mensaje enviado: '{hora_ultimo_salida}'")
                        break
                except:
                    continue

            # ── Paso 2: Leer mensajes entrantes posteriores al de salida ────────
            for fila in reversed(filas):
                try:
                    # Detenerse al encontrar el último mensaje de salida
                    if fila.find_elements(By.CSS_SELECTOR, "div.message-out"):
                        print("[Monitor] Encontrado último mensaje enviado por nosotros. Deteniendo lectura.")
                        break

                    if fila.find_elements(By.CSS_SELECTOR, "div.message-in"):
                        # Extraer texto
                        try:
                            texto_elem = fila.find_element(By.CSS_SELECTOR, "span.copyable-text")
                            texto = texto_elem.text
                        except:
                            texto = fila.text

                        # Extraer hora del mensaje entrante
                        hora_entrada = self._extraer_hora_fila(fila)

                        if texto:
                            texto = texto.strip()
                            if texto:
                                 # ── Filtro de respuesta automática (doble condición) ──
                                # Auto-respuesta = hora igual AL mensaje de salida
                                #                  Y mensaje largo (≥ 70 chars)
                                # Si solo se cumple UNA condición → se notifica (respuesta real)
                                hora_igual = (hora_ultimo_salida != "" and hora_entrada == hora_ultimo_salida)
                                mensaje_largo = len(texto) >= 70

                                if hora_igual and mensaje_largo:
                                    print(
                                        f"[Monitor] ⚡ Auto-respuesta detectada "
                                        f"(hora='{hora_entrada}' igual a salida, {len(texto)} chars ≥ 70) "
                                        f"→ OMITIENDO: '{texto[:50]}...'"
                                        if len(texto) > 50 else
                                        f"[Monitor] ⚡ Auto-respuesta detectada "
                                        f"(hora='{hora_entrada}' igual a salida, {len(texto)} chars ≥ 70) "
                                        f"→ OMITIENDO: '{texto}'"
                                    )
                                    continue  # No agregar a la lista

                                # Log cuando hora es igual pero mensaje corto (respuesta real)
                                if hora_igual and not mensaje_largo:
                                    print(
                                        f"[Monitor] ✅ Hora igual a salida pero mensaje corto "
                                        f"({len(texto)} chars < 70) → respuesta real, se notifica"
                                    )


                                mensajes_nuevos.append(f"[{hora_entrada}] {texto}" if hora_entrada else texto)

                                if len(mensajes_nuevos) >= MAX_MENSAJES_SIN_SALIDA and not hora_ultimo_salida:
                                    print(
                                        f"[Monitor] Alcanzado límite de {MAX_MENSAJES_SIN_SALIDA} mensajes "
                                        f"sin encontrar mensaje de salida. Deteniendo lectura."
                                    )
                                    break

                except Exception:
                    continue

            # Invertir para orden cronológico (antiguo → reciente)
            mensajes_nuevos.reverse()

            print(f"[Monitor] Leídos {len(mensajes_nuevos)} mensajes nuevos del cliente.")
            return mensajes_nuevos

        except Exception as e:
            print(f"[Monitor] Error al leer mensajes del chat: {e}")
            return []

    def contar_mensajes_salida(self):
        """
        Cuenta el número total de mensajes de salida (enviados por nosotros) en el chat actual.
        Esto permite determinar el nivel de interacción con el chat.
        
        Returns:
            int: Número de mensajes de salida encontrados
        """
        try:
            print("[Monitor] Contando mensajes de salida...")
            # Buscar TODOS los contenedores de mensajes
            filas = self.driver.find_elements(By.CSS_SELECTOR, "div[role='row']")
            
            contador_salida = 0
            
            for fila in filas:
                try:
                    # Verificar si es mensaje de salida (nuestro)
                    es_salida = fila.find_elements(By.CSS_SELECTOR, "div.message-out")
                    if es_salida:
                        contador_salida += 1
                except:
                    continue
            
            print(f"[Monitor] Total de mensajes de salida: {contador_salida}")
            return contador_salida
            
        except Exception as e:
            print(f"[Monitor] Error contando mensajes de salida: {e}")
            return 0
