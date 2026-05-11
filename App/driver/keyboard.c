/* Copyright 2025 muzkr https://github.com/muzkr
 * Copyright 2023 Manuel Jinger
 * Copyright 2023 Dual Tachyon
 * https://github.com/DualTachyon
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 *     Unless required by applicable law or agreed to in writing, software
 *     distributed under the License is distributed on an "AS IS" BASIS,
 *     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *     See the License for the specific language governing permissions and
 *     limitations under the License.
 */

#include "driver/gpio.h"
#include "driver/keyboard.h"
#include "driver/systick.h"
#include "driver/i2c.h"
#include "misc.h"

KEY_Code_t gKeyReading0     = KEY_INVALID;
KEY_Code_t gKeyReading1     = KEY_INVALID;
uint16_t   gDebounceCounter = 0;
bool       gWasFKeyPressed  = false;

#ifdef ENABLE_FEAT_F4HWN_SCREENSHOT
// Short press: hold key for SERIAL_KEY_SHORT_POLLS calls.
// Must exceed key_debounce_10ms (2) to trigger ProcessKey(key, true, false).
#define SERIAL_KEY_SHORT_POLLS  3

// Long press: hold key for SERIAL_KEY_LONG_POLLS calls.
// Must exceed key_repeat_delay_10ms (40) to trigger ProcessKey(key, true, true).
#define SERIAL_KEY_LONG_POLLS   41

volatile KEY_Code_t gKeyFromSerial      = KEY_INVALID;
static   uint8_t    gSerialKeyHoldCount = 0;
static   uint8_t    gSerialKeyLong      = 0;  // 0 = short press, 1 = long press
static   uint8_t    gSerialKeyForced    = 0;  // 1 while a key-down state is active over serial

typedef struct {
    KEY_Code_t key;
    uint8_t    isLong;
} SerialKeyEvent_t;

#define SERIAL_KEY_QUEUE_SIZE 32
static SerialKeyEvent_t gSerialKeyQueue[SERIAL_KEY_QUEUE_SIZE];
static uint8_t          gSerialKeyQueueHead = 0;
static uint8_t          gSerialKeyQueueTail = 0;

static bool KEYBOARD_EnqueueSerialKey(KEY_Code_t key, uint8_t isLong)
{
    uint8_t nextHead = (uint8_t)((gSerialKeyQueueHead + 1) % SERIAL_KEY_QUEUE_SIZE);

    // Queue full: drop the oldest key so the newest user input is kept responsive.
    if (nextHead == gSerialKeyQueueTail)
        gSerialKeyQueueTail = (uint8_t)((gSerialKeyQueueTail + 1) % SERIAL_KEY_QUEUE_SIZE);

    gSerialKeyQueue[gSerialKeyQueueHead].key    = key;
    gSerialKeyQueue[gSerialKeyQueueHead].isLong = isLong;
    gSerialKeyQueueHead = nextHead;
    return true;
}

static bool KEYBOARD_DequeueSerialKey(KEY_Code_t *key, uint8_t *isLong)
{
    if (gSerialKeyQueueHead == gSerialKeyQueueTail)
        return false;

    *key    = gSerialKeyQueue[gSerialKeyQueueTail].key;
    *isLong = gSerialKeyQueue[gSerialKeyQueueTail].isLong;
    gSerialKeyQueueTail = (uint8_t)((gSerialKeyQueueTail + 1) % SERIAL_KEY_QUEUE_SIZE);
    return true;
}

// Inject a short press from serial (UART or VCP).
// PTT is allowed so host tools (combined.py/combinedqt.py) can drive TX.
// Release is still guaranteed by KEYBOARD_Poll() once the hold-count expires.
void KEYBOARD_InjectKey(uint8_t keyCode)
{
    if (keyCode < KEY_INVALID) {
        KEYBOARD_EnqueueSerialKey((KEY_Code_t)keyCode, 0);
    }
}

// Inject a long press from serial (UART or VCP).
// PTT is allowed so host tools can emulate press-and-hold transmit.
void KEYBOARD_InjectKeyLong(uint8_t keyCode)
{
    if (keyCode < KEY_INVALID) {
        KEYBOARD_EnqueueSerialKey((KEY_Code_t)keyCode, 1);
    }
}

void KEYBOARD_SetSerialKeyState(uint8_t keyCode, bool isLong, bool isPressed)
{
    if (keyCode >= KEY_INVALID)
        return;

    if (isPressed) {
        gKeyFromSerial      = (KEY_Code_t)keyCode;
        gSerialKeyLong      = isLong ? 1u : 0u;
        gSerialKeyHoldCount = 0;
        gSerialKeyForced    = 1;
    } else if (gSerialKeyForced && gKeyFromSerial == (KEY_Code_t)keyCode) {
        gKeyFromSerial      = KEY_INVALID;
        gSerialKeyLong      = 0;
        gSerialKeyHoldCount = 0;
        gSerialKeyForced    = 0;
    }
}

bool KEYBOARD_IsSerialKeyHeld(KEY_Code_t key)
{
    return gSerialKeyForced && gKeyFromSerial == key;
}

#endif

#define GPIOx               GPIOB
#define PIN_MASK_COLS       (LL_GPIO_PIN_6 | LL_GPIO_PIN_5 | LL_GPIO_PIN_4 | LL_GPIO_PIN_3)
#define PIN_COLS            GPIO_MAKE_PIN(GPIOx, PIN_MASK_COLS)
#define PIN_COL(n)          GPIO_MAKE_PIN(GPIOx, 1u << (6 - (n)))

#define PIN_MASK_ROWS       (LL_GPIO_PIN_15 | LL_GPIO_PIN_14 | LL_GPIO_PIN_13 | LL_GPIO_PIN_12)
#define PIN_MASK_ROW(n)     (1u << (15 - (n)))

static inline uint32_t read_rows()
{
    return PIN_MASK_ROWS & LL_GPIO_ReadInputPort(GPIOx);
}

static const KEY_Code_t keyboard[5][4] = {
    {   // Zero col
        // Set to zero to handle special case of nothing pulled down
        KEY_SIDE1, 
        KEY_SIDE2, 

        // Duplicate to fill the array with valid values
        KEY_INVALID, 
        KEY_INVALID, 
    },
    {   // First col
        KEY_MENU, 
        KEY_1, 
        KEY_4, 
        KEY_7, 
    },
    {   // Second col
        KEY_UP, 
        KEY_2 , 
        KEY_5 , 
        KEY_8 , 
    },
    {   // Third col
        KEY_DOWN, 
        KEY_3   , 
        KEY_6   , 
        KEY_9   , 
    },
    {   // Fourth col
        KEY_EXIT, 
        KEY_STAR, 
        KEY_0   , 
        KEY_F   , 
    }
};

KEY_Code_t KEYBOARD_Poll(void)
{
#ifdef ENABLE_FEAT_F4HWN_SCREENSHOT
    // Serial-injected key: hold it for SHORT or LONG polls depending on press type,
    // so the debounce counter in app.c reaches the right threshold:
    //   - Short: key_debounce_10ms (2)  → ProcessKey(key, true, false)
    //   - Long:  key_repeat_delay_10ms (40) → ProcessKey(key, true, true)
    // Once the hold count is exhausted we clear it — next call returns KEY_INVALID,
    // which triggers the release path in app.c naturally.
    if (!gSerialKeyForced && gKeyFromSerial == KEY_INVALID) {
        KEY_Code_t nextKey;
        uint8_t    nextIsLong;
        if (KEYBOARD_DequeueSerialKey(&nextKey, &nextIsLong)) {
            gKeyFromSerial      = nextKey;
            gSerialKeyHoldCount = 0;
            gSerialKeyLong      = nextIsLong;
        }
    }

    if (gKeyFromSerial != KEY_INVALID) {
        KEY_Code_t injected  = gKeyFromSerial;
        uint8_t    threshold = gSerialKeyLong ? SERIAL_KEY_LONG_POLLS : SERIAL_KEY_SHORT_POLLS;

        // Latency optimization for remote TX hold (PTT long packet):
        // trigger initial press quickly, then rely on repeated host packets
        // to keep PTT asserted instead of waiting ~400ms for long-press timing.
        if (gSerialKeyLong && injected == KEY_PTT)
            threshold = SERIAL_KEY_SHORT_POLLS;
        if (!gSerialKeyForced && ++gSerialKeyHoldCount >= threshold) {
            gKeyFromSerial      = KEY_INVALID;
            gSerialKeyHoldCount = 0;
            gSerialKeyLong      = 0;
        }
        return injected;
    }
#endif

    KEY_Code_t Key = KEY_INVALID;

    // Scan all 5 columns - never break early to avoid GPIO state issues
    for (unsigned int j = 0; j < 5; j++)
    {
        uint32_t reg;
        uint32_t match_count = 0;  // Count consecutive matching reads

        // Set all columns high first
        GPIO_SetOutputPin(PIN_COLS);

        // Clear the specific column we are selecting
        if (j > 0)
        {
            GPIO_ResetOutputPin(PIN_COL(j - 1));
        }

        // Debounce: Read rows multiple times and look for stable reads
        // CRITICAL FIX #1: Proper debounce logic (replaces confusing i *= syntax)
        // CRITICAL FIX #2: Increased delay from 1µs to 10µs for real keyboard bounce capture
        // CRITICAL FIX #3: Clear match_count if reads differ (noise detection)
        reg = 0;
        for (unsigned int k = 0; k < 8; k++)
        {
            SYSTICK_DelayUs(10);  // FIX #2: Increased from 1µs to 10µs
            uint32_t reg2 = read_rows();
            
            // FIX #3: Clear match counter if values don't match
            if (reg2 != reg)
            {
                match_count = 0;
                reg = reg2;
            }
            else
            {
                match_count++;
            }
            
            // Success: We have 3 consecutive matching reads = stable signal
            if (match_count >= 2)
            {
                break;  // Debounce complete for this column
            }
        }

        // FIX #1: Do NOT break on noise - continue scanning all columns
        // Only skip key detection if debounce failed, but GPIO state is cleaned
        if (match_count < 2)
        {
            // Debounce failed (too much noise), but continue to next column
            // This prevents GPIO from staying in invalid state and blocking other columns
            continue;
        }

        // Debounce successful, check which row is pressed in this column
        for (unsigned int i = 0; i < 4; i++)
        {
            if (!(reg & PIN_MASK_ROW(i)))
            {
                Key = keyboard[j][i];
                break;
            }
        }

        if (Key != KEY_INVALID)
        {
            break;  // Found a valid key, stop scanning
        }
    }

    // CRITICAL FIX #4: Always clean up GPIO state - set all columns high at end
    // This ensures GPIO pins are in a known state even if function exited early due to noise
    GPIO_SetOutputPin(PIN_COLS);

    return Key;
}
