import os
import sys
import signal
import threading
from time import sleep, time
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from multiprocessing import cpu_count, Manager
import sqlite3
import requests
from bit import Key
from bloom_filter import BloomFilter  # requires the "bloom_filter" package

# Helper function to check address existence in SQLite
def address_exists_in_db(address, db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM addresses WHERE address = ?", (address,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

class Btcbf:
    def __init__(self):
        self.start_t = 0
        self.prev_n = 0
        self.cur_n = 0
        self.start_n = 0
        self.end_n = 0
        self.seq = False
        self.privateKey = None

        # Shutdown flags for threading and multiprocessing
        self.found_event = threading.Event()
        manager = Manager()
        self.mp_found_event = manager.Event()

        # Setup SQLite database for addresses
        self.db_path = os.path.join(os.getcwd(), "addresses.db")
        self._setup_db()

        # Create or ensure cache.txt exists
        cache_path = os.path.join(os.getcwd(), "cache.txt")
        if not os.path.exists(cache_path):
            with open(cache_path, "w+") as f:
                pass

        # Create a persistent session for online requests
        self.session = requests.Session()

        # Initialize a Bloom filter and load addresses from SQLite.
        # Adjust max_elements and error_rate based on your dataset size and acceptable false positive rate.
        self.bloom = BloomFilter(max_elements=10_000_000, error_rate=0.001)
        self.load_bloom_filter()

    def _setup_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS addresses (address TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

    def load_bloom_filter(self):
        """Load all addresses from the SQLite DB into the Bloom filter."""
        print("Loading addresses into Bloom filter...")
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT address FROM addresses")
        rows = cur.fetchall()
        for row in rows:
            self.bloom.add(row[0])
        conn.close()
        print(f"Loaded {len(rows)} addresses into the Bloom filter.")

    def import_addresses(self, filename):
        """Import addresses from a file into the SQLite database and reload the Bloom filter."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            count = 0
            with open(filename, "r") as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and lines containing 'wallet'
                    if not line or "wallet" in line:
                        continue
                    try:
                        cur.execute("INSERT OR IGNORE INTO addresses (address) VALUES (?)", (line,))
                        count += 1
                    except Exception as e:
                        print("Error inserting address:", line, e)
                conn.commit()
            conn.close()
            print(f"Imported {count} addresses into the database.")
            # Reload Bloom filter with updated addresses
            self.bloom = BloomFilter(max_elements=10_000_000, error_rate=0.001)
            self.load_bloom_filter()
        except Exception as e:
            print("Error during import:", e)

    def format_elapsed(self, elapsed):
        td = timedelta(seconds=int(elapsed))
        return str(td)

    def speed(self):
        while not self.found_event.is_set():
            if self.cur_n != 0:
                cur_t = time()
                n = self.cur_n
                if self.prev_n == 0:
                    self.prev_n = n
                elapsed_t = cur_t - self.start_t
                rate = abs(n - self.prev_n) // 2  # rough rate calculation over a 2-second sleep
                formatted_time = self.format_elapsed(elapsed_t)
                total = n - self.start_r
                print(f"Current n: {n}, Rate: {rate}/s, Elapsed: {formatted_time}, Total: {total}    ", end="\r")
                self.prev_n = n
                if self.seq:
                    with open("cache.txt", "w") as f:
                        f.write(f"{self.cur_n}-{self.start_r}-{self.end_n}")
            sleep(2)

    def record_found_key(self, key, message):
        print("\n" + message)
        print("Public Address: " + key.address)
        print("Private Key: " + key.to_wif())
        with open("foundkey.txt", "a") as f:
            f.write(key.address + "\n")
            f.write(key.to_wif() + "\n")

    def random_brute(self, n):
        if self.found_event.is_set():
            return
        self.cur_n = n
        key = Key()
        # Use Bloom filter first; if positive then check SQLite for confirmation.
        if key.address in self.bloom:
            if address_exists_in_db(key.address, self.db_path):
                self.record_found_key(key, "Wow matching address found!!")
                self.found_event.set()

    def sequential_brute(self, n):
        if self.mp_found_event.is_set():
            return
        self.cur_n = n
        key = Key().from_int(n)
        if key.address in self.bloom:
            if address_exists_in_db(key.address, self.db_path):
                self.record_found_key(key, "Wow matching address found!!")
                self.mp_found_event.set()
                self.found_event.set()

    def random_online_brute(self, n):
        if self.found_event.is_set():
            return
        self.cur_n = n
        key = Key()
        try:
            url = f"https://blockchain.info/q/getreceivedbyaddress/{key.address}/"
            response = self.session.get(url, timeout=10)
            the_page = response.text.strip()
            if the_page.isdigit() and int(the_page) > 0:
                self.record_found_key(key, "Wow active address found!!")
                self.found_event.set()
        except Exception as e:
            print(f"Error during online request: {e}")

    def num_of_cores(self):
        available_cores = cpu_count()
        try:
            cores_input = input(
                f"\nNumber of available cores: {available_cores}\n"
                "How many cores to be used? (leave empty to use all available cores)\nType something> "
            )
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received during input. Exiting.")
            sys.exit(0)

        if cores_input == "":
            self.cores = available_cores
        elif cores_input.isdigit():
            cores = int(cores_input)
            if 0 < cores <= available_cores:
                self.cores = cores
            elif cores <= 0:
                print(f"Hey you can't use {cores} number of CPU cores!")
                input("Press Enter to exit")
                raise ValueError("Negative number!")
            elif cores > available_cores:
                print(f"\nYou only have {available_cores} cores")
                confirm = input(f"Are you sure you want to use {cores} cores? [y/n]> ")
                if confirm.lower() == "y":
                    self.cores = cores
                else:
                    print("Using available number of cores")
                    self.cores = available_cores
        else:
            print("Wrong input!")
            input("Press Enter to exit")
            sys.exit(0)
        return self.cores

    def generate_random_address(self):
        key = Key()
        print("\nPublic Address: " + key.address)
        print("Private Key: " + key.to_wif())

    def generate_address_fromKey(self):
        if self.privateKey:
            try:
                key = Key(self.privateKey)
                print("\nPublic Address: " + key.address)
                print("\nYour wallet is ready!")
            except Exception as e:
                print("\nIncorrect key format:", e)
        else:
            print("No entry")

    def run_brute_force(self, target_func, range_gen, executor_class=ThreadPoolExecutor):
        cores = self.num_of_cores()
        self.start_t = time()
        self.start_r = next(iter(range_gen))
        with executor_class(max_workers=cores) as executor:
            for i in range_gen:
                if self.found_event.is_set() or self.mp_found_event.is_set():
                    break
                executor.submit(target_func, i)

    def get_user_input(self):
        try:
            user_input = input(
                "\nWhat do you want to do? \n"
                "  [1]: Generate random key pair \n"
                "  [2]: Generate public address from private key \n"
                "  [3]: Brute force bitcoin offline mode \n"
                "  [4]: Brute force bitcoin online mode \n"
                "  [5]: Import addresses from file into SQLite database \n"
                "  [0]: Exit \n"
                "Type something> "
            )
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received. Exiting.")
            sys.exit(0)

        if user_input == "1":
            self.generate_random_address()
            print("\nYour wallet is ready!")
            input("\nPress Enter to exit")
            return
        elif user_input == "2":
            self.privateKey = input("\nEnter Private Key> ")
            self.generate_address_fromKey()
            input("Press Enter to exit")
            return
        elif user_input == "3":
            method_input = input(
                "\nEnter the desired number: \n"
                "  [1]: Random attack \n"
                "  [2]: Sequential attack \n"
                "  [0]: Exit \n"
                "Type something> "
            )
            if method_input == "1":
                print("\nStarting random offline brute force ...")
                self.run_brute_force(self.random_brute, range(0, 10**18), ThreadPoolExecutor)
            elif method_input == "2":
                cache_path = "cache.txt"
                if os.path.getsize(cache_path) > 0:
                    with open(cache_path, "r") as f:
                        cache_content = f.read().strip()
                    if cache_content:
                        r0 = cache_content.split("-")
                        print(f"Resuming range {r0[0]} - {r0[2]}")
                        start_val = int(r0[0])
                        end_val = int(r0[2])
                        self.seq = True
                        self.start_r = int(r0[1])
                        print("\nResuming sequential offline brute force ...")
                        self.run_brute_force(self.sequential_brute, range(start_val, end_val), ProcessPoolExecutor)
                else:
                    range0 = input("\nEnter range in decimals (example: 1-100)> ")
                    r0 = range0.split("-")
                    if len(r0) == 2:
                        start_val = int(r0[0])
                        end_val = int(r0[1])
                    else:
                        start_val = int(r0[0])
                        end_val = int(r0[0])
                    with open("cache.txt", "w") as f:
                        f.write(f"{start_val}-{start_val}-{end_val}")
                    self.seq = True
                    self.start_r = start_val
                    print("\nStarting sequential offline brute force ...")
                    self.run_brute_force(self.sequential_brute, range(start_val, end_val), ProcessPoolExecutor)
            else:
                print("Exiting...")
                return
        elif user_input == "4":
            method_input = input(
                "\nEnter the desired number: \n"
                "  [1]: Random attack \n"
                "  [2]: Sequential attack \n"
                "  [0]: Exit \n"
                "Type something> "
            )
            if method_input == "1":
                print("\nStarting random online brute force ...")
                self.start_t = time()
                self.start_n = 0
                self.run_brute_force(self.random_online_brute, range(0, 10**18), ThreadPoolExecutor)
            elif method_input == "2":
                print("Sequential online attack will be available soon!")
                input("Press Enter to exit")
                return
            else:
                print("Exiting...")
                return
        elif user_input == "5":
            filename = input("\nEnter the filename to import addresses from> ")
            self.import_addresses(filename)
            input("Press Enter to exit")
            return
        elif user_input == "0":
            print("Exiting...")
            sleep(2)
            return
        else:
            print("No valid input. Generating random key pair by default.")
            self.generate_random_address()
            print("Your wallet is ready!")
            input("Press Enter to exit")
            return

def signal_handler(signum, frame):
    print("\nSignal handler triggered. Shutting down gracefully...")
    obj.found_event.set()
    obj.mp_found_event.set()
    sys.exit(0)

if __name__ == "__main__":
    # Set up signal handling for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    obj = Btcbf()
    try:
        speed_thread = threading.Thread(target=obj.speed, daemon=True)
        speed_thread.start()
        obj.get_user_input()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt caught in main. Exiting...")
        sys.exit(0)
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        print("Terminating program.")
